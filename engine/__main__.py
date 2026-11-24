import sys
from .server import serve

if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        serve(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        print("usage: python3 -m engine serve [port]")
