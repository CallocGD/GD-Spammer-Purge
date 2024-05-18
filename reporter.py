import asyncio
from click.termui import style
import click
from typing import TypeVar, Coroutine, Any, Callable, Awaitable
from typing_extensions import ParamSpec
from entrypoint import entrypoint





T = TypeVar("T")
P = ParamSpec("P")




LOADING_ANIM = [
    "[=   ]", 
    "[ =  ]", 
    "[  = ]",
    "[   =]",
    "[    ]"
]

class Reporter:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.queue = asyncio.Queue()
        self.idx = 0
        self.message = "Scanning For Spam "
        self.pass_context = False
    
    async def loading(self):
        async with self.lock:
            print(style(self.message + LOADING_ANIM[self.idx], fg="bright_yellow", reset=True), end="\r")
        self.idx += 1
        if self.idx >= len(LOADING_ANIM):
            self.idx = 0

    def pending(self, msg:str):
        self.queue.put_nowait(style("[...] %s" % msg, fg="bright_blue", reset=True))

    def success(self, msg:str):
        self.queue.put_nowait(style("[+] %s" % msg, fg="bright_green", reset=True))

    def warning(self, msg:str):
        self.queue.put_nowait(style("[!] Warning %s" % msg, fg="bright_red", reset=True))

    def error(self, msg:str):
         self.queue.put_nowait(style("[ERROR] %s " % msg, fg="bright_white", bg="red", reset=True))

    async def update_default_message(self, title:str):
        async with self.lock:
            self.clear()
            self.message = title


    def clear(self):
        print(" " * (len(self.message) + 6), end="\r")
    
    async def poll(self):
        if not self.queue.empty():
            self.clear()
            print(self.queue.get_nowait())
            self.queue.task_done()
        else:
            # print(self.__dict__)
            await self.loading()

    async def wait(self, coro:Coroutine[Any, Any, T]) -> T:
        """Runs while task is not completed yet"""
        task = asyncio.create_task(coro)
        while not task.done():
            await self.poll()
            await asyncio.sleep(.2)

        # Drain the queue out if we still have some items leftover...
        while not self.queue.empty():
            self.clear()
            print(self.queue.get_nowait())
            self.queue.task_done()
        
        return await task
    
    async def run(self, func:Callable[P, Awaitable[T]], *args:P.args, **kwargs:P.kwargs) -> T:
        """Runs the main loop until finished while passing this context off as `reporter` to whatever the async function is"""
        if self.pass_context:
            return await self.wait(func(reporter=self, *args, **kwargs))
        else:
            return await self.wait(func(*args, **kwargs))

async def test_task(reporter:Reporter):
    await reporter.update_default_message("Waiting 2 seonds")
    reporter.pending("Pending...")
    await asyncio.sleep(2)
    reporter.success("2 second wait was sucessful")
    await reporter.update_default_message("Wating 1 second")
    await asyncio.sleep(1)
    reporter.success("Wait Succeeded")


@entrypoint(__name__)
def main():
    rep = Reporter()
    rep.pass_context = True
    asyncio.run(rep.run(test_task))






