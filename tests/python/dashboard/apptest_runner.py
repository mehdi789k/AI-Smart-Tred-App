import json
import sys

# Small helper script to run a Streamlit AppTest in a subprocess and emit JSON
# This isolates thread/cleanup-related KeyboardInterrupts that can crash the test
# runner process executing pytest.
from streamlit.testing.v1 import AppTest


def main():
    if len(sys.argv) < 3:
        print("Usage: apptest_runner.py <app_path> <timeout_seconds> [page]")
        sys.exit(3)

    path = sys.argv[1]
    timeout = int(sys.argv[2])
    page = sys.argv[3] if len(sys.argv) > 3 else None

    import contextlib
    import io

    pages = []

    # Run initial load to discover pages (best-effort). Capture stdout so AppTest
    # internal prints / app logs don't pollute the JSON payload on stdout.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        app = AppTest.from_file(path).run(timeout=timeout)
    # Forward captured logs to stderr so the test harness can still record them
    sys.stderr.write(buf.getvalue())

    try:
        pages = list(app.sidebar.radio[0].options)
    except Exception:
        pages = []

    # If a specific page is requested, set the radio value and run again
    if page:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            app = AppTest.from_file(path).run(timeout=timeout)
            try:
                app.sidebar.radio[0].set_value(page).run(timeout=timeout)
            except Exception as e:
                app.exception = e
        sys.stderr.write(buf.getvalue())

    result = {
        "exception": bool(getattr(app, "exception", None)),
        "exception_repr": repr(getattr(app, "exception", None)) if getattr(app, "exception", None) else None,
        "pages": pages,
    }

    # Emit JSON to stdout for the parent pytest process to parse
    sys.stdout.write(json.dumps(result))
    sys.exit(0 if not result["exception"] else 2)


if __name__ == "__main__":
    main()
