import sys
import time


def calc():
    with open("data.txt") as f:
        lines = f.readlines()
    print(f"Read {len(lines)} lines from data.txt")


def depend_check():
    try:
        import numpy

        print("Numpy is installed and version:", numpy.__version__)
    except ImportError as e:
        print("Dependency missing!", file=sys.stderr)
        raise


def conda_depend_check():
    try:
        import pandas as pd

        df = pd.DataFrame({"test": [1, 2, 3]})
        print("Pandas DataFrame:\n", df)
    except ImportError as e:
        print("Conda dependency missing!", file=sys.stderr)
        raise


def simulate_long_job():
    for i in range(3):
        print(f"Simulating step {i+1}/3 ...")
        time.sleep(2)


def cause_error():
    print("Intentionally causing an error for error handling test.")
    raise RuntimeError("Pilot code error: this is a test.")


if __name__ == "__main__":
    print("-- PILOT RUN BEGIN --")
    calc()
    depend_check()
    conda_depend_check()
    simulate_long_job()
    # Uncomment below to pilot error recovery:
    # cause_error()
    print("-- PILOT RUN DONE --")
