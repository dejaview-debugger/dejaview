import socket


def print_bytes(data):
    # Prints the given data in hexadecimal, 16 bytes per line for readability.
    for i in range(0, len(data), 16):
        chunk = data[i : min(i + 16, len(data))]
        print(" ".join(f"{b:02x}" for b in chunk))


def query_dns(domain: str, qtype: int, qclass: int):
    """
    Simulates a DNS query by returning pre-defined results for certain domain names.
    Parameters:
        domain: The domain name being queried.
        qtype: The query type (e.g., A, AAAA, etc.).
        qclass: The query class (e.g., IN for internet).
    Returns:
        A tuple (TTL, List of IP addresses) if the domain is found, otherwise (0, []).
    """
    if qtype == 1 and qclass == 1:  # A IN (IPv4 Internet query)
        match domain:
            case "google.com":
                return 260, ["192.165.1.1", "192.165.1.10"]
            case "youtube.com":
                return 160, ["192.165.1.2"]
            case "uwaterloo.ca":
                return 160, ["192.165.1.3"]
            case "wikipedia.org":
                return 160, ["192.165.1.4"]
            case "amazon.ca":
                return 160, ["192.165.1.5"]
    return 0, []  # Domain not found


def decode_request(request: bytes):
    """
    Decodes a DNS query request.
    Parameters:
        request: The raw DNS query packet as bytes.
    Returns:
        A tuple containing the query ID, domain name, query type, and query class.
    """
    id = int.from_bytes(request[:2], "big")  # Extracts the transaction ID.
    domain = []
    i = 12  # DNS query starts at byte 12.
    while request[i] != 0:  # 0 indicates the end of the domain name.
        length = request[i]
        domain.append(request[(i + 1) : (i + 1 + length)].decode("ascii"))
        i += 1 + length
    qtype = int.from_bytes(request[i + 1 : i + 3], "big")  # Query type (e.g., A, AAAA).
    qclass = int.from_bytes(request[i + 3 : i + 5], "big")  # Query class (e.g., IN).
    domain = ".".join(domain)  # Reconstruct the full domain name.
    return id, domain.lower(), qtype, qclass


def handle_request(request):
    """
    Processes a DNS query request and generates a response.
    Parameters:
        request: The raw DNS query packet as bytes.
    Returns:
        The raw DNS response packet as bytes.
    """
    id, domain, qtype, qclass = decode_request(request)  # Decode the DNS query.
    ttl, ips = query_dns(domain, qtype, qclass)  # Look up the domain.

    # Construct DNS response flags.
    qr = 1  # Response (not a query).
    opcode = 0  # Standard query.
    aa = 1  # Authoritative answer.
    tc = 0  # Not truncated.
    rd = 0  # Recursion not desired.
    ra = 0  # Recursion not available.
    z = 0  # Reserved.
    rcode = 0  # No error.

    flags = (
        (qr << 15)
        | (opcode << 11)
        | (aa << 10)
        | (tc << 9)
        | (rd << 8)
        | (ra << 7)
        | (z << 4)
        | rcode
    )

    # Header section of the DNS response.
    qdcount = 1  # Number of questions (always 1 here).
    ancount = len(ips)  # Number of answers.
    nscount = 0  # Number of authority records.
    arcount = 0  # Number of additional records.
    header = [id, flags, qdcount, ancount, nscount, arcount]
    header = b"".join(x.to_bytes(2, "big") for x in header)

    question = request[12:]  # Reuse the question section from the request.

    # Build the answer section.
    answers = b""
    for ip in ips:
        name = b"\xc0\x0c"  # Pointer to the domain name in the question section.
        rdlength = 4  # Length of the RDATA (IPv4 address).
        rdata = socket.inet_aton(ip)  # Convert IP address to binary format.
        answers += (
            name
            + qtype.to_bytes(2, "big")
            + qclass.to_bytes(2, "big")
            + ttl.to_bytes(4, "big")
            + rdlength.to_bytes(2, "big")
            + rdata
        )

    return header + question + answers


server_addr = ("127.0.0.1", 10086)

# UDP server to handle DNS queries.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.bind(server_addr)
    # s.settimeout(1)  # Timeout to avoid indefinite blocking.

    while True:
        try:
            # Receive a request from a client.
            request, addr = s.recvfrom(1024)
            print("Request:")
            print_bytes(request)

            # Process the request and prepare the response.
            response = handle_request(request)
            print("Response:")
            print_bytes(response)

            # Send the response back to the client.
            s.sendto(response, addr)
            pass
        except socket.timeout:
            pass  # Ignore timeouts to continue the server loop.
        except KeyboardInterrupt:
            break  # Gracefully exit on Ctrl+C.
