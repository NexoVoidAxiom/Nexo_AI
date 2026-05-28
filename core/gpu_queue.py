"""
core/gpu_queue.py — Cola de inferencia GPU para Void Axiom.
===========================================================
Garantía central: si la GPU está ocupada o sin VRAM, las tareas
se encolan — el sistema NUNCA crashea por OOM.

Arquitectura:
  · Un único asyncio.Semaphore(1) serializa acceso a la GPU.
  · GPUQueue.submit() acepta cualquier coroutine y la encola con prioridad.
  · Si la cola está llena (MAX_QUEUE_DEPTH), retorna QueueFullError
    para que el API devuelva 503 en vez de colgar indefinidamente.
  · OOMRetryError: si Ollama devuelve error de VRAM, el job se reencola
    automáticamente hasta MAX_OOM_RETRIES veces con backoff exponencial.

Uso típico (desde dispatcher.py):
    gpu_queue = GPUQueue()

    async def my_handler(request):
        result = await gpu_queue.submit(my_inference_coro(args), priority=1)
        return result

Prioridades (menor = más urgente):
    0 = PIONEER / admin
    1 = plan_max
    2 = plan_free (default)
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

log = logging.getLogger("void.gpu_queue")

T = TypeVar("T")

# ── Configuración ─────────────────────────────────────────────────────────────
MAX_QUEUE_DEPTH = 32        # Máximo de jobs en espera antes de rechazar
MAX_OOM_RETRIES = 3         # Reintentos ante errores de VRAM
OOM_BACKOFF_BASE = 2.0      # Segundos base para backoff exponencial
GPU_TIMEOUT = 120.0         # Timeout máximo por job (segundos)


# ── Excepciones propias ───────────────────────────────────────────────────────

class QueueFullError(RuntimeError):
    """La cola de GPU está llena. El cliente debe recibir HTTP 503."""


class OOMRetryError(RuntimeError):
    """Se agotaron los reintentos por OOM. El job fue descartado."""


class GPUJobTimeout(asyncio.TimeoutError):
    """El job superó GPU_TIMEOUT segundos sin completarse."""


# ── Job interno ───────────────────────────────────────────────────────────────

@dataclass(order=True)
class _GPUJob:
    """
    Job priorizado para la cola del heap.
    `sort_index` combina prioridad + timestamp para garantizar FIFO
    dentro de la misma prioridad.
    """
    sort_index: tuple = field(compare=True)
    coro_fn:    Callable[[], Awaitable[Any]] = field(compare=False)
    future:     asyncio.Future               = field(compare=False)
    oom_count:  int                          = field(default=0, compare=False)

    @classmethod
    def create(
        cls,
        coro_fn: Callable[[], Awaitable[Any]],
        priority: int = 2,
    ) -> "_GPUJob":
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        return cls(
            sort_index = (priority, time.monotonic()),
            coro_fn    = coro_fn,
            future     = future,
        )


# ── Cola principal ────────────────────────────────────────────────────────────

class GPUQueue:
    """
    Cola de inferencia GPU con prioridades y manejo de OOM.

    El worker corre como tarea de fondo desde el arranque del servidor
    y procesa un job a la vez bajo el semaphore.
    """

    def __init__(self, concurrency: int = 1) -> None:
        self._sem      = asyncio.Semaphore(concurrency)
        self._heap: list[_GPUJob] = []
        self._heap_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._running = False

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Arrancar el worker en background. Llamar en startup de FastAPI."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="gpu_queue_worker"
        )
        log.info("GPUQueue iniciada (concurrency=%d, max_depth=%d)",
                 self._sem._value, MAX_QUEUE_DEPTH)

    async def stop(self) -> None:
        """Detener el worker limpiamente. Llamar en shutdown de FastAPI."""
        self._running = False
        self._heap_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        log.info("GPUQueue detenida.")

    # ── API pública ────────────────────────────────────────────────────────

    async def submit(
        self,
        coro_fn: Callable[[], Awaitable[T]],
        priority: int = 2,
        timeout: float = GPU_TIMEOUT,
    ) -> T:
        """
        Encola una coroutine y espera su resultado.

        Args:
            coro_fn:  Callable sin argumentos que devuelve una coroutine.
                      Se llama como lambda para poder ser reencolar ante OOM.
            priority: 0=pioneer, 1=max, 2=free
            timeout:  Segundos máximos de espera total (cola + ejecución)

        Returns:
            El resultado de coro_fn()

        Raises:
            QueueFullError:  Si la cola supera MAX_QUEUE_DEPTH.
            OOMRetryError:   Si se agotan reintentos por OOM.
            GPUJobTimeout:   Si el job supera `timeout` segundos.
        """
        if len(self._heap) >= MAX_QUEUE_DEPTH:
            log.warning("Cola GPU llena (%d jobs). Rechazando solicitud.", len(self._heap))
            raise QueueFullError(
                f"Cola de GPU llena ({MAX_QUEUE_DEPTH} jobs en espera). "
                "Inténtalo en unos segundos."
            )

        job = _GPUJob.create(coro_fn, priority)
        heapq.heappush(self._heap, job)
        self._heap_event.set()

        log.debug("Job encolado (prioridad=%d, queue_len=%d)", priority, len(self._heap))

        try:
            return await asyncio.wait_for(job.future, timeout=timeout)
        except asyncio.TimeoutError:
            job.future.cancel()
            raise GPUJobTimeout(
                f"Job GPU superó {timeout}s. Puede que el modelo esté cargando."
            )

    @property
    def queue_length(self) -> int:
        return len(self._heap)

    @property
    def is_busy(self) -> bool:
        return self._sem._value == 0

    # ── Worker interno ─────────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Procesa jobs del heap uno a uno bajo el semaphore."""
        while self._running:
            # Esperar hasta que haya algo en la cola
            if not self._heap:
                self._heap_event.clear()
                await self._heap_event.wait()
                continue

            job = heapq.heappop(self._heap)

            # Si el future fue cancelado (timeout del cliente), descartar
            if job.future.cancelled():
                continue

            async with self._sem:
                await self._execute_job(job)

    async def _execute_job(self, job: _GPUJob) -> None:
        """Ejecuta un job con manejo de OOM y retry."""
        attempt = job.oom_count + 1
        try:
            result = await job.coro_fn()
            if not job.future.cancelled():
                job.future.set_result(result)

        except Exception as exc:
            if _is_oom_error(exc) and job.oom_count < MAX_OOM_RETRIES:
                backoff = OOM_BACKOFF_BASE ** job.oom_count
                log.warning(
                    "OOM detectado (intento %d/%d). Reencol·ando en %.1fs...",
                    attempt, MAX_OOM_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)

                # Reencolar con misma prioridad (se "envejece" el timestamp)
                retry_job = _GPUJob.create(job.coro_fn, priority=job.sort_index[0])
                retry_job = _GPUJob(
                    sort_index = (job.sort_index[0], time.monotonic()),
                    coro_fn    = job.coro_fn,
                    future     = job.future,   # ← mismo future, el caller sigue esperando
                    oom_count  = job.oom_count + 1,
                )
                heapq.heappush(self._heap, retry_job)
                self._heap_event.set()

            else:
                # OOM agotado o error distinto: propagar al caller
                if not job.future.cancelled():
                    if _is_oom_error(exc) and job.oom_count >= MAX_OOM_RETRIES:
                        job.future.set_exception(
                            OOMRetryError(
                                f"Se agotaron {MAX_OOM_RETRIES} reintentos por OOM. "
                                "Libera VRAM o reduce el contexto."
                            )
                        )
                    else:
                        job.future.set_exception(exc)
                log.error("Job GPU falló tras %d intento(s): %s", attempt, exc)


def _is_oom_error(exc: Exception) -> bool:
    """Detecta errores de Out-of-Memory de CUDA/Ollama."""
    msg = str(exc).lower()
    return any(keyword in msg for keyword in (
        "out of memory",
        "cuda out of memory",
        "cuda error",
        "oom",
        "not enough memory",
        "failed to allocate",
        "vram",
    ))


# ── Singleton global ──────────────────────────────────────────────────────────
# Importar desde dispatcher.py o main.py:
#   from void_axiom.core.gpu_queue import gpu_queue
gpu_queue = GPUQueue(concurrency=1)
