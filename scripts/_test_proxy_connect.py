import socket

for host in ("host.docker.internal", "172.17.0.1", "172.19.0.1"):
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((host, 8888))
        print(host, "8888 OK")
    except Exception as e:
        print(host, "8888 FAIL", e)
    finally:
        s.close()
