import logging

from server.services.chunking.assets import assets_dir, prefetch

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    prefetch()
    print(f'assets ready in {assets_dir()}')
