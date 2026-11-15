import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .database import import_dump
from .dumps import download, download_pinned
from .site import build
from .embeddings import DEFAULT_ENDPOINT, DEFAULT_MODEL, compute


def main():
    parser = argparse.ArgumentParser(description='Romanian etymology explorer')
    sub = parser.add_subparsers(dest='command', required=True)
    dl = sub.add_parser('download', help='Download and SHA-1 verify Wikimedia XML')
    dl.add_argument('--edition', choices=['ro', 'en'], default='ro')
    dl.add_argument('--directory', type=Path, default=Path('data/dumps'))
    dl.add_argument('--force', action='store_true')
    dl.add_argument('--source', type=Path, help='Versioned JSON manifest for an exact reproducible snapshot')
    imp = sub.add_parser('import', help='Rebuild SQLite atomically from a full XML dump')
    imp.add_argument('dump', type=Path)
    imp.add_argument('--edition', choices=['ro', 'en'], default='ro')
    imp.add_argument('--database', type=Path, default=Path('data/dic.sqlite'))
    site = sub.add_parser('build', help='Generate a standalone HTML explorer')
    site.add_argument('--database', type=Path, default=Path('data/dic.sqlite'))
    site.add_argument('--output', type=Path, default=Path('dist'))
    site.add_argument('--vectors', type=Path, default=Path('data/vectors.sqlite'))
    embed = sub.add_parser('embed', help='Cache embeddings and word/etymon distances on local disk')
    embed.add_argument('--database', type=Path, default=Path('data/dic.sqlite'))
    embed.add_argument('--vectors', type=Path, default=Path('data/vectors.sqlite'))
    embed.add_argument('--dump', type=Path, help='Also extract attested definitions from this same dump')
    embed.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    embed.add_argument('--model', default=DEFAULT_MODEL)
    embed.add_argument('--batch-size', type=int, default=64)
    serve = sub.add_parser('serve', help='Serve the generated SPA')
    serve.add_argument('--directory', type=Path, default=Path('dist'))
    serve.add_argument('--host', default='0.0.0.0')
    serve.add_argument('--port', default=8040, type=int)
    args = parser.parse_args()
    if args.command == 'download':
        if args.source:
            download_pinned(args.source, args.directory)
        else:
            download(args.directory, args.edition, args.force)
    elif args.command == 'import':
        import_dump(args.dump, args.database, args.edition)
    elif args.command == 'build':
        build(args.database, args.output, args.vectors)
    elif args.command == 'embed':
        compute(args.database, args.vectors, args.dump, args.endpoint, args.model, args.batch_size)
    else:
        if not (args.directory / 'index.html').is_file():
            parser.error('No generated page: run rake populate and rake build first')
        server = ThreadingHTTPServer((args.host, args.port), partial(SimpleHTTPRequestHandler, directory=str(args.directory)))
        print(f'Conventum: http://{args.host}:{server.server_port}', flush=True)
        if args.host == '0.0.0.0':
            print(f'Listening on all IPv4 interfaces. Open http://<server-hostname-or-IP>:{args.port} from your browser.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
