"""Repair breakdowns must reconcile, while persisted global estimates still load."""
import pytest
from pydantic import ValidationError

from claim_api.astra_analysis import AccidentAssessment, CostedDamageFinding, RepairEstimate, strict_schema


def estimate(**overrides):
    return {
        'minimum_minor': 200000, 'maximum_minor': 400000,
        'currency': 'EUR', 'assumptions': 'Dégâts cachés exclus.',
        'line_items': [
            {'label': 'Pare-chocs', 'minimum_minor': 150000, 'maximum_minor': 300000},
            {'label': 'Peinture et pose', 'minimum_minor': 50000, 'maximum_minor': 100000},
        ], **overrides,
    }


def test_repair_breakdown_reconciles_in_cents():
    result = RepairEstimate.model_validate(estimate())
    assert sum(item.minimum_minor for item in result.line_items) == result.minimum_minor
    assert sum(item.maximum_minor for item in result.line_items) == result.maximum_minor


@pytest.mark.parametrize('overrides', [
    {'minimum_minor': 199999},
    {'maximum_minor': 400001},
    {'line_items': [{'label': 'Poste', 'minimum_minor': 400000, 'maximum_minor': 200000}]},
    {'line_items': [{'label': 'Poste', 'minimum_minor': -1, 'maximum_minor': 400000}]},
])
def test_inconsistent_or_invalid_repair_costs_are_rejected(overrides):
    with pytest.raises(ValidationError):
        RepairEstimate.model_validate(estimate(**overrides))


def test_existing_global_estimates_remain_readable_without_invented_items():
    previous = estimate()
    del previous['line_items']
    assert RepairEstimate.model_validate(previous).line_items == []


def test_provider_schema_requests_the_breakdown():
    schema = strict_schema(AccidentAssessment)
    assert 'line_items' in schema['$defs']['RepairEstimate']['required']
    assert schema['$defs']['RepairLineItem']['additionalProperties'] is False
    assert schema['$defs']['CostedDamageFinding']['properties']['estimate']['anyOf'][0]['$ref'] == '#/$defs/RepairEstimate'


def damage(**overrides):
    return {
        'vehicle': 'Peugeot', 'description': 'Pare-chocs et feu arrière endommagés.',
        'affected_parts': ['Pare-chocs', 'Feu arrière gauche'], 'severity': 'moderate',
        'accident_link': 'consistent', 'confidence': 'high',
        'citations': [{'evidence_id': '00000000-0000-0000-0000-000000000001'}],
        'estimate': estimate(), **overrides,
    }


def test_damage_costs_are_itemized_independently_of_insurance_role():
    result = CostedDamageFinding.model_validate(damage())
    assert result.estimate.line_items[0].label == 'Pare-chocs'
    assert result.estimate.minimum_minor == 200000


def test_vehicle_damage_totals_must_reconcile_too():
    with pytest.raises(ValidationError, match='sum of the line items'):
        CostedDamageFinding.model_validate(damage(estimate=estimate(minimum_minor=1)))


def test_existing_damage_estimates_still_load_without_a_breakdown():
    previous = estimate()
    del previous['line_items']
    assert CostedDamageFinding.model_validate(damage(estimate=previous)).estimate.line_items == []
