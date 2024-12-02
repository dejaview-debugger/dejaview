import socket
import random


def encode_domain(domain: str) -> bytes:
    """
    Encodes a domain name into the DNS wire format.
    Parameters:
        domain: The domain name to encode (e.g., "example.com").
    Returns:
        A bytes object containing the encoded domain name.
    """
    parts = domain.split(".")
    assert all(0 < len(part) < 256 for part in parts), "Invalid domain"
    # Each part is prefixed by its length and ends with a null byte.
    return (
        b"".join(bytes([len(part)]) + part.encode("ascii") for part in parts) + b"\x00"
    )


def decode_response(request: bytes):
    """
    Decodes a DNS response packet.
    Parameters:
        request: The raw DNS response packet as bytes.
    Returns:
        A tuple containing the transaction ID, domain name, query type/class,
        and a list of answers.
    """
    id = int.from_bytes(request[:2], "big")  # Extract transaction ID.

    # Decode question section
    domain = []
    i = 12  # The question section starts at byte 12.
    while request[i] != 0:  # Read until null byte indicating end of domain name.
        length = request[i]
        domain.append(request[(i + 1) : (i + 1 + length)].decode("ascii"))
        i += 1 + length
    qtype = int.from_bytes(request[i + 1 : i + 3], "big")  # Query type.
    qclass = int.from_bytes(request[i + 3 : i + 5], "big")  # Query class.
    domain = ".".join(domain)

    # Decode answer section
    ancount = int.from_bytes(request[6:8], "big")  # Number of answers.
    i += 5  # Move past the end of the question section.
    answers = []
    for _ in range(ancount):
        atype = int.from_bytes(request[i + 2 : i + 4], "big")  # Answer type.
        aclass = int.from_bytes(request[i + 4 : i + 6], "big")  # Answer class.
        ttl = int.from_bytes(request[i + 6 : i + 10], "big")  # Time to live.
        rdlength = int.from_bytes(request[i + 10 : i + 12], "big")  # Data length.
        i += 12
        rdata = request[i : i + rdlength]  # Answer data (e.g., IP address).
        i += rdlength
        answers.append((atype, aclass, ttl, rdata))

    return id, domain, qtype, qclass, answers


def print_response(response: bytes):
    """
    Parses and prints the DNS response in a human-readable format.
    Parameters:
        response: The raw DNS response packet as bytes.
    """
    id, domain, qtype, qclass, answers = decode_response(response)
    for atype, aclass, ttl, rdata in answers:
        # Assuming type A (IPv4), convert the binary address to a readable string.
        print(
            f"> {domain}: type A, class IN, TTL {ttl}, addr ({len(rdata)}) {socket.inet_ntoa(rdata)}"
        )


def generate_request(domain: str) -> bytes:
    """
    Generates a DNS query packet for a given domain.
    Parameters:
        domain: The domain name to query (e.g., "example.com").
    Returns:
        A bytes object containing the raw DNS query packet.
    """
    id = random.randrange(1 << 16)  # Random transaction ID.

    # Construct flags for the DNS header.
    qr = 0  # Query (not a response).
    opcode = 0  # Standard query.
    aa = 1  # Authoritative answer (not used here, always 1 for simplicity).
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

    # Header section of the DNS request.
    qdcount = 1  # Number of questions (always 1 here).
    ancount = 0  # Number of answers (0 for a query).
    nscount = 0  # Number of authority records (not used).
    arcount = 0  # Number of additional records (not used).
    header = [id, flags, qdcount, ancount, nscount, arcount]
    header = b"".join(x.to_bytes(2, "big") for x in header)

    # Construct question section.
    qname = encode_domain(domain.lower())
    qtype = 1  # Query type A (IPv4 address).
    qclass = 1  # Query class IN (Internet).
    question = qname + qtype.to_bytes(2, "big") + qclass.to_bytes(2, "big")

    return header + question


server_addr = ("127.0.0.1", 10086)  # Server address and port.

# UDP client to send and receive DNS queries.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
    s.settimeout(1)  # Set timeout to avoid indefinite blocking.
    while True:
        try:
            domain = input("> Enter Domain Name: ")
            if domain == "end":
                raise KeyboardInterrupt  # End the session on "end" input.
            request = generate_request(domain)  # Create a DNS query.
            s.sendto(request, server_addr)  # Send the query to the server.
            response, _ = s.recvfrom(1024)  # Receive the response.
            print_response(response)  # Parse and print the response.
        except socket.timeout:
            pass  # Ignore timeout errors to keep the session running.
        except KeyboardInterrupt:
            print("Session ended")  # Graceful exit on Ctrl+C or "end".
            break
