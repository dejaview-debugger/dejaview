import random
import time

from ..dejaview import DejaView, print_handler


def foo():
    print("foo")


def test():
    print("start")
    for i in range(10):
        if i == 8:
            print("breakpoint 1")
            breakpoint()
            print("after breakpoint 1")  # should stop here
        print(i)
        foo()
        print("random", i)
        random.randint(0, 10)
    print("end1")
    print("end2")
    print("end3")
    return 42


def test_input():
    print("start")
    s1 = input("enter input 1: ")
    print("input 1:", s1)
    breakpoint()
    s2 = input("enter input 2: ")
    print("input 1:", s1)
    print("input 2:", s2)
    print("finish")


def test_time():
    print("start")
    s = time.time()
    print("time:", s)
    breakpoint()
    print("time:", s)
    elapsed = time.time() - s
    print("elapsed time:", elapsed)
    print("finish")


dejaview = DejaView()
# dejaview.counter.add_handler(print_handler)
with dejaview:
    test_input()

print("Number of frames:", dejaview.counter.count)
