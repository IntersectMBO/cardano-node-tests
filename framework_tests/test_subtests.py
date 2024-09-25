import pytest
import pytest_subtests


class TestSubtests:
    def test_outcomes(
        self,
        subtests: pytest_subtests.SubTests,
    ):
        xfail_msgs = []

        # In subtest, don't return any other outcome than success or failure.
        # Allure doesn't work well with subtests. It will use outcome of the first non-successful
        # subtest as the overall test outcome.
        # Therefore skiped / xfailed subtests could mask subtest failures. As a workaround,
        # record the outcome of the subtest and use it as the outcome of the main test.
        def _subtest(num: int) -> None:
            if num > 200:
                xfail_msgs.append(f"{num} > 200")

        for n in (100, 200, 300, 500):
            with subtests.test(msg="check num", num=n):
                _subtest(n)

        if xfail_msgs:
            pytest.xfail("; ".join(xfail_msgs))
