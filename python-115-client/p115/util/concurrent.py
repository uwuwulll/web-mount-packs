#!/usr/bin/env python3
# encoding: utf-8

__author__ = "ChenyangGao <https://chenyanggao.github.io>"
__all__ = ["thread_batch", "thread_pool_batch", "async_batch", "as_thread"]

from asyncio import CancelledError, Semaphore, TaskGroup
from collections.abc import Callable, Coroutine, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial, update_wrapper
from inspect import getfullargspec, isawaitable
from queue import Queue
from threading import Event, Lock, Thread
from typing import cast, Any, Optional, TypeVar


T = TypeVar("T")
V = TypeVar("V")


def argcount(func: Callable) -> int:
    try:
        return func.__code__.co_argcount
    except AttributeError:
        return len(getfullargspec(func).args)


def thread_batch(
    work: Callable[[T], V] | Callable[[T, Callable], V], 
    tasks: Iterable[T], 
    callback: Optional[Callable[[V], Any]] = None, 
    workers: int = 1, 
):
    ac = argcount(work)
    if ac < 1:
        raise TypeError(f"{work!r} should accept a positional argument as task")
    with_submit = ac > 1
    if workers <= 0:
        workers = 1
    sentinal = object()
    q: Queue[T | object] = Queue()
    get, put, task_done = q.get, q.put, q.task_done
    def worker():
        task: T | object
        while (task := get()) is not sentinal:
            task = cast(T, task)
            try:
                if with_submit:
                    r = cast(Callable[[T, Callable], V], work)(task, put)
                else:
                    r = cast(Callable[[T], V], work)(task)
                if callback is not None:
                    callback(r)
            except BaseException:
                pass
            task_done()
        put(sentinal)
    for task in tasks:
        put(task)
    for _ in range(workers):
        Thread(target=worker).start()
    try:
        q.join()
    finally:
        q.queue.clear()
        put(sentinal)


def thread_pool_batch(
    work: Callable[[T], V] | Callable[[T, Callable], V], 
    tasks: Iterable[T], 
    callback: Optional[Callable[[V], Any]] = None, 
    max_workers: Optional[int] = None, 
):
    ac = argcount(work)
    if ac < 1:
        raise TypeError(f"{work!r} should take a positional argument as task")
    with_submit = ac > 1
    n = 0
    lock = Lock()
    done_evt = Event()
    def works(task):
        nonlocal n
        try:
            if with_submit:
                r = cast(Callable[[T, Callable], V], work)(task, submit)
            else:
                r = cast(Callable[[T], V], work)(task)
            if callback is not None:
                callback(r)
        finally:
            with lock:
                n -= 1
            if not n:
                done_evt.set()
    def submit(task):
        nonlocal n
        with lock:
           n += 1
        return create_task(works, task)
    pool = ThreadPoolExecutor(max_workers)
    try:
        create_task = pool.submit
        for task in tasks:
            submit(task)
        done_evt.wait()
    finally:
        pool.shutdown(False, cancel_futures=True)


async def async_batch(
    work: Callable[[T], Coroutine[None, None, V]] | Callable[[T, Callable], Coroutine[None, None, V]], 
    tasks: Iterable[T], 
    callback: Optional[Callable[[V], Any]] = None, 
    sema: Optional[Semaphore] = None, 
):
    ac = argcount(work)
    if ac < 1:
        raise TypeError(f"{work!r} should accept a positional argument as task")
    with_submit = ac > 1
    async def works(task):
        try:
            if sema is None:
                if with_submit:
                    r = await cast(Callable[[T, Callable], Coroutine[None, None, V]], work)(task, submit)
                else:
                    r = await cast(Callable[[T], Coroutine[None, None, V]], work)(task)
            else:
                async with sema:
                    if with_submit:
                        r = await cast(Callable[[T, Callable], Coroutine[None, None, V]], work)(task, submit)
                    else:
                        r = await cast(Callable[[T], Coroutine[None, None, V]], work)(task)
            if callback is not None:
                t = callback(r)
                if isawaitable(t):
                    await t
        except KeyboardInterrupt:
            raise
        except BaseException as e:
            raise CancelledError from e
    def submit(task):
        return create_task(works(task))
    async with TaskGroup() as tg:
        create_task = tg.create_task
        for task in tasks:
            submit(task)


def as_thread(
    func: Optional[Callable[..., V]] = None, 
    /, 
    **thread_init_kwds, 
):
    if func is None:
        return partial(as_thread, **thread_init_kwds)
    def wrapper(*args, **kwds) -> Future[V]: 
        def asfuture(): 
            try: 
                fu.set_result(func(*args, **kwds))
            except BaseException as e:
                fu.set_exception(e)
        fu: Future[V] = Future()
        Thread(target=asfuture, **thread_init_kwds).start()
        return fu
    return update_wrapper(wrapper, func)

