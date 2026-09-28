"""Runs real Django model tests against a disposable SQLite database."""

import sys
import tempfile
from unittest.mock import patch

from tests.viewer_test_bootstrap import ViewerTestEnvironment


def run_tests(test_labels=None):
    """
    Runs selected Django tests with the isolated external-operation guards

    :param list test_labels: Optional Django test labels
    :return: Process exit status"""
    with tempfile.TemporaryDirectory(prefix="corpo-viewer-model-tests-") as directory:
        with ViewerTestEnvironment(directory, allow_models=True):
            import django
            from django.test.runner import DiscoverRunner

            # Python's Windows platform probe can launch a subprocess. Celery
            # only needs the OS family during import; derive that family from
            # the running interpreter while retaining the process guard. This
            # does not validate Celery's unmodified platform detection.
            if sys.platform == "win32":
                with patch("platform.system", return_value="Windows"):
                    django.setup()
            else:
                django.setup()
            runner = DiscoverRunner(verbosity=2, interactive=False, parallel=0)
            failures = runner.run_tests(
                test_labels or ["tests.test_model_test_bootstrap"],
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_tests(sys.argv[1:]))
