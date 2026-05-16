from tradingagents.cn.services.compliance_service import ComplianceService


def test_compliance_rewrites_common_advice_expression():
    result = ComplianceService().review_text("建议买入并设置目标价。")

    assert result.violations
    assert result.rewritten_text is not None
    assert "建议买入" not in result.rewritten_text
    assert "目标价" not in result.rewritten_text


def test_compliance_approves_neutral_text():
    result = ComplianceService().review_text("该公告披露了公司的经营事实，后续需阅读正式公告。")

    assert result.approved
    assert result.violations == []
