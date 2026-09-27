from claim_api import camerci_cameras


INDEX = "CSV,MAIL,NOM,SOURCE\nparis,mail@example.invalid,Paris,http://example.invalid\n"
CAMERAS = "X,Y,NOM\n2.3501,48.8501,Caméra proche\n2.355,48.855,Caméra lointaine\n"


def _clear_cache() -> None:
    camerci_cameras._cache.clear()
    camerci_cameras._catalog = None


def test_map_lookup_uses_camerci_catalogue_and_caches_it(monkeypatch) -> None:
    _clear_cache()
    calls = []

    def fake_request(url):
        calls.append(url)
        if "nominatim" in url:
            return '[{"lat":"48.85","lon":"2.35","addresstype":"road","display_name":"Rue test, Paris"}]'
        if url.endswith("index.csv"):
            return INDEX
        return CAMERAS

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    result = camerci_cameras.CamerciCameraLocator().search("Rue test, Paris")
    assert result.status == "available"
    assert [camera.label for camera in result.cameras] == ["Caméra proche"]
    assert result.cameras[0].operator == "Paris"
    assert result.cameras[0].source_url.startswith("https://camerci.fr/#17/")
    assert camerci_cameras.CamerciCameraLocator().search("Rue test, Paris") == result
    assert len(calls) == 3


def test_50m_lookup_returns_only_nearest_camera_and_keeps_radius_cache_separate(monkeypatch) -> None:
    _clear_cache()
    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"48.85","lon":"2.35","addresstype":"road","display_name":"Rue test, Paris","address":{"city":"Paris"}}]'
        if url.endswith("index.csv"):
            return INDEX
        return "X,Y,NOM\n2.3501,48.8501,Proche\n2.351,48.8501,Plus loin\n"
    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    locator = camerci_cameras.CamerciCameraLocator()
    near = locator.search("Rue test, Paris", radius_m=50)
    wide = locator.search("Rue test, Paris", radius_m=500)
    assert near.radius_m == 50
    assert [camera.label for camera in near.cameras] == ["Proche"]
    assert wide.radius_m == 500
    assert wide.cameras[0].distance_m <= wide.cameras[1].distance_m



def test_babylone_150m_includes_the_camera_excluded_by_50m(monkeypatch) -> None:
    _clear_cache()

    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"48.8517285","lon":"2.3210618","addresstype":"house","address":{"city":"Paris"}}]'
        if url.endswith("index.csv"):
            return INDEX
        return ("X,Y,NOM\n"
                "2.3242825339341553,48.85020698847428,Bac / Sèvres / St Placide\n"
                "2.321844864103488,48.85165084768522,23 rue de Babylone\n")

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    locator = camerci_cameras.CamerciCameraLocator()
    short = locator.search("30 rue de Babylone", radius_m=50)
    assert short.status == "available"
    assert short.radius_m == 50
    assert [camera.label for camera in short.cameras] == ["23 rue de Babylone"]
    assert short.cameras[0].distance_m == 58
    assert "hors du rayon" in short.reason

    normal = locator.search("30 rue de Babylone")
    assert normal.radius_m == 150
    assert normal.status == "available"
    assert [camera.label for camera in normal.cameras] == ["23 rue de Babylone"]
    assert normal.reason is None
    assert locator.search("30 rue de Babylone", radius_m=50) == short

    wide = locator.search("30 rue de Babylone", radius_m=500)
    assert len(wide.cameras) == 2
    assert [camera.distance_m for camera in wide.cameras] == [58, 290]


def test_empty_catalogue_does_not_invent_a_fallback_camera(monkeypatch) -> None:
    _clear_cache()

    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"48.85","lon":"2.35","addresstype":"road"}]'
        return INDEX if url.endswith("index.csv") else "X,Y,NOM\n"

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    result = camerci_cameras.CamerciCameraLocator().search("Rue test, Paris")
    assert result.status == "unavailable"
    assert result.cameras == []


def test_uncovered_region_is_not_reported_as_no_cameras(monkeypatch) -> None:
    _clear_cache()

    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"50.636","lon":"3.063","addresstype":"road"}]'
        return INDEX if url.endswith("index.csv") else CAMERAS

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    result = camerci_cameras.CamerciCameraLocator().search("Rue Faidherbe, Lille")
    assert result.status == "not_covered"
    assert len(result.cameras) == 1
    assert result.cameras[0].distance_m > result.radius_m
    assert "hors du rayon" in result.reason


def test_nearby_suburb_is_not_assumed_covered(monkeypatch) -> None:
    _clear_cache()

    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"48.86","lon":"2.35","addresstype":"road","address":{"city":"Montreuil"}}]'
        return INDEX if url.endswith("index.csv") else CAMERAS

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    assert camerci_cameras.CamerciCameraLocator().search("Rue test, Montreuil").status == "not_covered"


def test_city_level_and_missing_addresses_do_not_load_camerci(monkeypatch) -> None:
    _clear_cache()
    calls = []

    def fake_request(url):
        calls.append(url)
        return '[{"lat":"48.85","lon":"2.35","addresstype":"city"}]'

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    locator = camerci_cameras.CamerciCameraLocator()
    assert locator.search(None).status == "unresolved"
    assert locator.search("Paris, France").status == "unresolved"
    assert len(calls) == 1


def test_catalogue_failure_does_not_claim_no_cameras(monkeypatch) -> None:
    _clear_cache()

    def fake_request(url):
        if "nominatim" in url:
            return '[{"lat":"48.85","lon":"2.35","addresstype":"road"}]'
        raise OSError("catalogue unavailable")

    monkeypatch.setattr(camerci_cameras, "_text_request", fake_request)
    assert camerci_cameras.CamerciCameraLocator().search("Rue test, Paris").status == "unavailable"


def test_map_endpoint_uses_the_authenticated_case_address(monkeypatch) -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from claim_api.auth import AuthenticatedUser, get_current_user
    from claim_api.camera_service import CameraService
    from claim_api.fixtures import SCENARIOS
    from claim_api.main import create_app
    from claim_api.models import CameraMapView, CaseView
    from claim_api.routes.cameras import get_camera_service

    owner, case_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    case = CaseView(id=case_id, created_by_user_id=owner, scenario_id="complete",
                    synthetic=True, status="collecting", state_version=1,
                    content_revision=1, created_at=now, updated_at=now,
                    intake=SCENARIOS["complete"].intake.model_copy(deep=True))

    class Repository:
        def get_case(self, actor_id, requested_id):
            return case if actor_id == owner and requested_id == case_id else None

    seen = []

    def fake_search(_self, address, radius_m=150):
        seen.append((address, radius_m))
        return CameraMapView(status="not_covered", address=address,
                             latitude=50.636, longitude=3.063)

    monkeypatch.setattr(camerci_cameras.CamerciCameraLocator, "search", fake_search)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=str(owner))
    app.dependency_overrides[get_camera_service] = lambda: CameraService(Repository())
    response = TestClient(app).get(f"/v1/cases/{case_id}/cameras/map")
    assert response.status_code == 200
    assert response.json()["status"] == "not_covered"
    assert seen == [(case.intake.location, 150)]
    short = TestClient(app).get(f"/v1/cases/{case_id}/cameras/map?radius_m=50")
    assert short.status_code == 200
    assert seen[-1] == (case.intake.location, 50)
    assert TestClient(app).get(f"/v1/cases/{case_id}/cameras/map?radius_m=501").status_code == 422
    assert TestClient(app).get(f"/v1/cases/{uuid4()}/cameras/map").status_code == 404
