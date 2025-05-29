import uvicorn


from typing import Callable


class UvicornServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, on_startup: Callable):
        super().__init__(config)
        self.on_startup = on_startup

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.on_startup:
            await self.on_startup()
