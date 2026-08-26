from rmpp_manager.template_engine import safe_eval, template_variables


def test_safe_eval_arithmetic_and_variables():
    assert safe_eval("templateWidth / 3 + 10", {"templateWidth": 1620}) == 550


def test_safe_eval_comparison_and_ternary():
    variables = {
        "templateHeight": 2160,
        "mobileMaxWidth": 1000,
        "desktop": 750,
        "mobile": 300,
    }
    assert (
        safe_eval(
            "templateHeight > mobileMaxWidth ? desktop : mobile",
            variables,
        )
        == 750
    )


def test_constants_are_evaluated_in_order():
    template = {
        "constants": [
            {"midX": "templateWidth / 2"},
            {"right": "midX + 100"},
        ]
    }
    variables = template_variables(template, 1620, 2160)
    assert variables["midX"] == 810
    assert variables["right"] == 910
