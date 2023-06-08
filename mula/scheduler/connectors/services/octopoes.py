from typing import Dict, List

from scheduler.connectors.errors import exception_handler
from scheduler.models import OOI, Organisation

from .services import HTTPService


class Octopoes(HTTPService):
    name = "octopoes"
    health_endpoint = None

    def __init__(self, host: str, source: str, orgs: List[Organisation]):
        self.orgs: List[Organisation] = orgs
        super().__init__(host, source)

    @exception_handler
    def get_objects_by_object_types(
        self, organisation_id: str, object_types: List[str], scan_level: List[int]
    ) -> List[OOI]:
        """Get all oois from octopoes"""
        if scan_level is None:
            scan_level = []

        url = f"{self.host}/{organisation_id}/objects"

        params = {
            "types": object_types,
            "scan_level": {s for s in scan_level},
            "offset": 0,
            "limit": 1,
        }

        # Get the total count of objects
        response = self.get(url, params=params)
        count = response.json().get("count")

        # Set the limit
        limit = 1000
        params["limit"] = limit

        # Loop over the paginated results
        oois = []
        for offset in range(0, count, limit):
            params["offset"] = offset
            response = self.get(url, params=params)
            oois.extend([OOI(**ooi) for ooi in response.json().get("items", [])])

        return oois

    @exception_handler
    def get_random_objects(self, organisation_id: str, n: int, scan_level: List[int]) -> List[OOI]:
        """Get `n` random oois from octopoes"""

        if scan_level is None:
            scan_level = []

        url = f"{self.host}/{organisation_id}/objects/random"

        params = {
            "amount": str(n),
            "scan_level": {s for s in scan_level},
        }

        response = self.get(url, params=params)

        return [OOI(**ooi) for ooi in response.json()]

    @exception_handler
    def get_object(self, organisation_id: str, reference: str) -> OOI:
        """Get an ooi from octopoes"""
        url = f"{self.host}/{organisation_id}"
        response = self.get(url, params={"reference": reference})
        return OOI(**response.json())

    @exception_handler
    def get_findings_by_ooi(self, organisation_id: str, reference: str) -> List[Dict]:
        url = f"{self.host}/{organisation_id}/tree"
        response = self.get(url, params={"reference": reference, "depth": 2})

        tree = response.json()
        findings: List[Dict] = []
        for _, references in tree.root.children.finding.items():
            for finding in references:
                findings.append(tree.store[str(finding.reference)])

        return findings

    @exception_handler
    def get_children_by_ooi(self, organisation_id: str, reference: str) -> List[Dict]:
        url = f"{self.host}/{organisation_id}/tree"
        response = self.get(url, params={"reference": reference, "depth": 2})

        tree = response.json()

        children: List[Dict] = []
        for k, v in tree.store.items():
            if k == tree.root.reference:
                continue

            children.append(v)

        return children

    def is_healthy(self) -> bool:
        healthy = True
        for org in self.orgs:
            if not self.is_host_healthy(self.host, f"/{org.id}{self.health_endpoint}"):
                return False

        return healthy
