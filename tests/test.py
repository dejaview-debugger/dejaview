import time

def test():
    def foo(x):
        print(4)
        print(5)
    print(1)
    breakpoint()
    print(2)
    foo(3)
    print(3)
    time.sleep(1)

test()
