import unittest
from unittest import mock

from scheduler import config
from scheduler.connectors import services
from scheduler.utils import remove_trailing_slash

from tests.factories import OrganisationFactory, PluginFactory


class BytesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config.settings.Settings()
        self.service_bytes = services.Bytes(
            host=remove_trailing_slash(str(self.config.host_bytes)),
            user=self.config.host_bytes_user,
            password=self.config.host_bytes_password,
            source="scheduler_test",
        )

    def test_login(self):
        self.service_bytes.login()

        # TODO: check headers

    def test_login_expired_token(self):
        pass

    # TODO: do we want to mock the response here?
    def test_get_last_run_boefje(self):
        pass

    # TODO: do we want to mock the response here?
    def test_get_last_run_boefje_by_organisation_id(self):
        pass


class KatalogusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = config.settings.Settings()
        self.service_katalogus = services.Katalogus(
            host=remove_trailing_slash(str(self.config.host_katalogus)),
            source="scheduler_test",
            cache_ttl=12345,
        )

    def tearDown(self) -> None:
        self.service_katalogus.organisations_plugin_cache.reset()
        self.service_katalogus.organisations_boefje_type_cache.reset()
        self.service_katalogus.organisations_normalizer_type_cache.reset()

    def test_get_organisations(self):
        orgs = self.service_katalogus.get_organisations()
        self.assertIsInstance(orgs, list)
        self.assertEqual(len(orgs), 0)

    @mock.patch("scheduler.connectors.services.Katalogus.get_organisations")
    def test_flush_organisations_plugin_cache(self, mock_get_organisations):
        # Mock
        mock_get_organisations.return_value = [
             OrganisationFactory(id="org-1"),
             OrganisationFactory(id="org-2"),
             OrganisationFactory(id="org-3"),
        ]

        # Act
        self.service_katalogus.flush_organisations_plugin_cache()

        # Assert
        self.assertEqual(len(self.service_katalogus.organisations_plugin_cache), 2)
        self.assertIsNotNone(self.service_katalogus.organisations_plugin_cache.get("org-1"))

    @mock.patch("scheduler.connectors.services.Katalogus.get_organisations")
    def test_flush_organisations_plugin_cache_empty(self, mock_get_organisations):
        # Mock
        mock_get_organisations.return_value = []

        # Act
        self.service_katalogus.flush_organisations_plugin_cache()

        # Assert
        self.assertEqual(len(self.service_katalogus.organisations_plugin_cache), 0)
        self.assertIsNone(self.service_katalogus.organisations_plugin_cache.get("org-1"))

    @mock.patch("scheduler.connectors.services.Katalogus.get_plugins_by_organisation")
    @mock.patch("scheduler.connectors.services.Katalogus.get_organisations")
    def test_flush_organisations_boefje_type_cache(self, mock_get_organisations, mock_get_plugins_by_organisation):
        # Mock
        mock_get_organisations.return_value = [
            OrganisationFactory(id="org-1"),
            OrganisationFactory(id="org-2"),
        ]

        mock_get_plugins_by_organisation.return_value = [
            PluginFactory(id="plugin-1", type="boefje", enabled=True, consumes=["Hostname"]),
            PluginFactory(id="plugin-2", type="boefje", enabled=True, consumes=["Hostname"]),
        ]

        # Act
        self.service_katalogus.flush_organisations_boefje_type_cache()

        # Assert
        self.assertEqual(len(self.service_katalogus.organisations_boefje_type_cache), 2)
        self.assertIsNotNone(self.service_katalogus.organisations_boefje_type_cache.get("org-1"))
        self.assertIsNotNone(self.service_katalogus.organisations_boefje_type_cache.get("org-1").get("Hostname"))
        self.assertEqual(len(self.service_katalogus.organisations_boefje_type_cache.get("org-1").get("Hostname")), 2)
        self.assertIsNotNone(self.service_katalogus.organisations_boefje_type_cache.get("org-2"))
        self.assertIsNotNone(self.service_katalogus.organisations_boefje_type_cache.get("org-2").get("Hostname"))
        self.assertEqual(len(self.service_katalogus.organisations_boefje_type_cache.get("org-2").get("Hostname")), 2)

    @mock.patch("scheduler.connectors.services.Katalogus.get_plugins_by_organisation")
    @mock.patch("scheduler.connectors.services.Katalogus.get_organisations")
    def test_flush_organisations_normalizer_type_cache(self, mock_get_organisations, mock_get_plugins_by_organisation):
        # Mock
        mock_get_organisations.return_value = [
            OrganisationFactory(id="org-1"),
            OrganisationFactory(id="org-2"),
        ]

        mock_get_plugins_by_organisation.return_value = [
            PluginFactory(id="plugin-1", type="normalizer", enabled=True, consumes=["Hostname"]),
            PluginFactory(id="plugin-2", type="normalizer", enabled=True, consumes=["Hostname"]),
        ]

        # Act
        self.service_katalogus.flush_organisations_normalizer_type_cache()

        # Assert
        self.assertEqual(len(self.service_katalogus.organisations_normalizer_type_cache), 2)
        self.assertIsNotNone(self.service_katalogus.organisations_normalizer_type_cache.get("org-1"))
        self.assertIsNotNone(self.service_katalogus.organisations_normalizer_type_cache.get("org-1").get("Hostname"))
        self.assertEqual(len(self.service_katalogus.organisations_normalizer_type_cache.get("org-1").get("Hostname")), 2)
        self.assertIsNotNone(self.service_katalogus.organisations_normalizer_type_cache.get("org-2"))
        self.assertIsNotNone(self.service_katalogus.organisations_normalizer_type_cache.get("org-2").get("Hostname"))
        self.assertEqual(len(self.service_katalogus.organisations_normalizer_type_cache.get("org-2").get("Hostname")), 2)

    def test_get_plugins_by_org_id(self):
        pass

    def test_get_plugins_by_org_id_expired(self):
        pass
