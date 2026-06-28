import asyncio

from loguru import logger


async def supervise(name: str, coro_fn, *args, restart: bool = True, **kwargs):
    log = logger.bind(component=name)
    while True:
        try:
            await coro_fn(*args, **kwargs)
        except asyncio.CancelledError:
            break
        except Exception as e:
            msg = f"Crashed: {e}"
            if restart:
                log.warning(f"{msg} — restarting in 5s")
                await asyncio.sleep(5)
            else:
                log.error(f"{msg} — not restarting")
                break
