from memex.evaluation.objective import verify_file_state


def test_verify_file_state_checks_required_expected_and_forbidden_content(tmp_path):
    (tmp_path / "calculator.py").write_text("def multiply(a, b):\n    return a * b\n", encoding="utf-8")

    ok, failures = verify_file_state(
        tmp_path,
        required_files=["calculator.py", "tests/test_calculator.py"],
        expected_text={"calculator.py": ["def multiply", "return a * b"]},
        forbidden_text={"calculator.py": ["TODO"]},
    )

    assert not ok
    assert "missing_file:tests/test_calculator.py" in failures


def test_verify_file_state_accepts_objective_state(tmp_path):
    (tmp_path / "calculator.py").write_text("def multiply(a, b):\n    return a * b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calculator.py").write_text("assert True\n", encoding="utf-8")

    assert verify_file_state(
        tmp_path,
        required_files=["calculator.py", "tests/test_calculator.py"],
        expected_text={"calculator.py": ["def multiply", "return a * b"]},
    ) == (True, [])
