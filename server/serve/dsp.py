"""Advertise the altermagnets database as a DSP 2025-1 minimal catalogue.

The catalogue publishes one DCAT dataset (the altermagnets database) whose sole
distribution is the interactive website, and advertises two ``dcat:DataService``s
that serve it: the OPTIMADE API and the website itself. DSP mandates HTTPS, so
the catalogue is only built for an HTTPS public origin.
"""

from httk.core import Dataset, DatasetDistribution, Service
from httk.serve.dsp import (
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationRecord,
    create_dsp_app,
)
from starlette.applications import Starlette

from .service import AMDB_DESCRIPTION, AMDB_NAME

# httk.serve.dsp only ships CSV/JSON access-point type IRIs; the website
# distribution is HTML, so its format/media-type IRIs are supplied explicitly.
EU_FILE_TYPE_HTML = "http://publications.europa.eu/resource/authority/file-type/HTML"
IANA_MEDIA_TYPE_HTML = "https://www.iana.org/assignments/media-types/text/html"
OPTIMADE_SPEC_IRI = "https://github.com/Materials-Consortia/OPTIMADE/blob/v1.3.0/optimade.rst"
WEBSITE_STANDARD_IRI = "https://schema.org/WebSite"
DSP_MOUNT = "/dsp"


def build_dsp_app(public_origin: str) -> Starlette:
    """Build the DSP 2025-1 minimal catalogue advertising the OPTIMADE API and website.

    :param public_origin: An absolute HTTPS origin without a path (e.g.
        ``https://altermagnets.anyterial.se``). DSP mandates HTTPS.
    """
    origin = public_origin.rstrip("/")
    participant_id = f"{origin}#participant"

    dataset = DspPublicationRecord(
        dataset=DspDatasetPublication(
            dataset=Dataset(
                id=f"{origin}/dataset",
                title=AMDB_NAME,
                description=AMDB_DESCRIPTION,
                publisher_id=participant_id,
                publisher_name="Anyterial",
                distributions=(
                    DatasetDistribution(
                        access_url=f"{origin}/",
                        format_iri=EU_FILE_TYPE_HTML,
                        media_type_iri=IANA_MEDIA_TYPE_HTML,
                    ),
                ),
            )
        )
    )
    optimade_service = DspPublicationRecord(
        service=Service(
            id=f"{origin}/optimade/amdb#dsp-service",
            title=f"{AMDB_NAME} OPTIMADE API",
            endpoint_url=f"{origin}/optimade/amdb",
            conforms_to=(OPTIMADE_SPEC_IRI,),
        )
    )
    website_service = DspPublicationRecord(
        service=Service(
            id=f"{origin}/#dsp-website-service",
            title=f"{AMDB_NAME} website",
            endpoint_url=f"{origin}/",
            conforms_to=(WEBSITE_STANDARD_IRI,),
        )
    )
    config = DspProviderConfig(
        public_base_url=origin,
        dsp_mount=DSP_MOUNT,
        service_id=f"{origin}/dsp#service",
        service_title="Anyterial DSP connector",
        participant_id=participant_id,
        catalog_id=f"{origin}/dsp/catalog",
        catalog_title=AMDB_NAME,
        catalog_description=AMDB_DESCRIPTION,
    )
    provider = DspProvider(config, publications=(dataset, optimade_service, website_service))
    return create_dsp_app(provider)
