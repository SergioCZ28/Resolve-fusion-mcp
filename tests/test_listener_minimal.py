"""
Minimal listener test -- paste this in Fusion's Py3 console to debug.
It tries to open the socket and reports any error.
"""
import socket
import threading
import time

def test_server():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("localhost", 9876))
        s.listen(1)
        print("[TEST] SUCCESS: Socket bound on localhost:9876")
        print("[TEST] Waiting 30 seconds for a connection...")
        s.settimeout(30)
        try:
            client, addr = s.accept()
            print(f"[TEST] Client connected from {addr}")
            client.close()
        except socket.timeout:
            print("[TEST] No client connected (timeout)")
        s.close()
    except Exception as e:
        print(f"[TEST] FAILED: {type(e).__name__}: {e}")

t = threading.Thread(target=test_server, daemon=True)
t.start()
print("[TEST] Thread started, waiting 2 seconds...")
time.sleep(2)
print("[TEST] Done -- check messages above")
