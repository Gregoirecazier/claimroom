from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from claim_api.astra_analysis import AccidentAssessment, strict_schema
from test_accident_journey import assessment_for


@pytest.fixture
def assessment():
    case = SimpleNamespace(evidence=[SimpleNamespace(id=uuid4())])
    entries = [{'id': str(uuid4()), 'frames': [{'seconds': 0.125}]}]
    return assessment_for(case, entries).model_dump(mode='json')


def test_old_reports_remain_readable_without_new_analysis(assessment):
    assessment.pop('involved_vehicles')
    assessment.pop('key_facts')
    result = AccidentAssessment.model_validate(assessment)
    assert result.involved_vehicles == [] and result.key_facts == []


def test_short_summary_allows_unknown_insurance_roles(assessment):
    assessment.update(insured_vehicle=None, involved_vehicles=['Peugeot', 'BMW'],
                      key_facts=['La BMW semble reculer vers la Peugeot.', 'Contact à l’arrière gauche.'])
    assessment['media']['plates'][0]['role'] = 'unknown'
    result = AccidentAssessment.model_validate(assessment)
    assert result.involved_vehicles == ['Peugeot', 'BMW']
    assert result.insured_vehicle is None


@pytest.mark.parametrize('pair', [['Peugeot', 'Inconnue'], ['Peugeot', 'Peugeot'], ['Peugeot'], ['Peugeot', 'BMW', 'Toyota']])
def test_pair_must_reference_the_two_main_observed_vehicles(assessment, pair):
    assessment['involved_vehicles'] = pair
    with pytest.raises(ValidationError):
        AccidentAssessment.model_validate(assessment)


@pytest.mark.parametrize('facts', [['x' * 181], ['a', 'b', 'c', 'd'], ['']])
def test_insurer_facts_are_bounded(assessment, facts):
    assessment['key_facts'] = facts
    with pytest.raises(ValidationError):
        AccidentAssessment.model_validate(assessment)


def test_provider_schema_requires_the_structured_summary():
    schema = strict_schema(AccidentAssessment)
    assert {'key_facts', 'involved_vehicles'} <= set(schema['required'])
    assert schema['properties']['key_facts']['items']['maxLength'] == 180
