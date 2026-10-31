"""Local serving adapter; keep derivative delivery outside the base producer's source identity."""
import runpy
from urllib.parse import urlparse
from turku3d.coarse import service
from scenery import Scenery, serve

base_handler = service.handler


def handler(builder, prefetcher=None):
    base = base_handler(builder, prefetcher)
    scenery = Scenery(builder.root)

    class Handler(base):
        def do_GET(self):
            if not serve(self, scenery, urlparse(self.path)):
                super().do_GET()

    return Handler


if __name__ == '__main__':
    service.handler = handler
    runpy.run_module('turku3d.coarse', run_name='__main__')
