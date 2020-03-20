from base64 import b64encode
from datetime import datetime
from typing import List, Tuple, cast
from functools import reduce
from pyfedex.ship_service_v25 import (
    CompletedShipmentDetail,
    ShipmentRateDetail,
    CompletedPackageDetail,
    ProcessShipmentRequest,
    TransactionDetail,
    VersionId,
    RequestedShipment,
    RequestedPackageLineItem,
    Party,
    Contact,
    Address,
    TaxpayerIdentification,
    Weight as FedexWeight,
    LabelSpecification,
    Dimensions as FedexDimensions,
    ServiceDescription,
    TrackingId,
    ShipmentSpecialServicesRequested,
    ShipmentEventNotificationDetail,
    ShipmentEventNotificationSpecification,
    NotificationDetail,
    EMailDetail,
    Localization,
    CodDetail,
    CodCollectionType,
    Money
)
from purplship.core.utils.helpers import export, concat_str
from purplship.core.utils.serializable import Serializable
from purplship.core.utils.soap import clean_namespaces, create_envelope
from purplship.core.utils.xml import Element
from purplship.core.units import Weight, WeightUnit, DimensionUnit, Dimension, Options
from purplship.core.models import ShipmentDetails, Error, ChargeDetails, ShipmentRequest
from purplship.carriers.fedex.error import parse_error_response
from purplship.carriers.fedex.utils import Settings
from purplship.carriers.fedex.units import PackagingType, ServiceType, SpecialServiceType


NOTIFICATION_EVENTS = ['ON_DELIVERY', 'ON_ESTIMATED_DELIVERY', 'ON_EXCEPTION', 'ON_SHIPMENT', 'ON_TENDER']


def parse_shipment_response(
    response: Element, settings: Settings
) -> Tuple[ShipmentDetails, List[Error]]:
    details = response.xpath(
        ".//*[local-name() = $name]", name="CompletedShipmentDetail"
    )
    shipment: ShipmentDetails = _extract_shipment(details[0], settings) if len(
        details
    ) > 0 else None
    return shipment, parse_error_response(response, settings)


def _extract_shipment(
    shipment_detail_node: Element, settings: Settings
) -> ShipmentDetails:
    detail = CompletedShipmentDetail()
    detail.build(shipment_detail_node)

    shipment_node = shipment_detail_node.xpath(
        ".//*[local-name() = $name]", name="ShipmentRateDetails"
    )[0]
    shipment = ShipmentRateDetail()
    shipment.build(shipment_node)

    items_nodes = shipment_detail_node.xpath(
        ".//*[local-name() = $name]", name="CompletedPackageDetails"
    )
    items: List[CompletedPackageDetail] = []
    for node in items_nodes:
        item = CompletedPackageDetail()
        item.build(node)
        items.append(item)
    tracking_number = cast(TrackingId, detail.MasterTrackingId).TrackingNumber
    service = ServiceType(cast(ServiceDescription, detail.ServiceDescription).ServiceType).name
    return ShipmentDetails(
        carrier=settings.carrier_name,
        service=service,
        tracking_number=tracking_number,
        total_charge=ChargeDetails(
            name="Shipment charge",
            amount=float(shipment.TotalNetChargeWithDutiesAndTaxes.Amount),
            currency=shipment.TotalNetChargeWithDutiesAndTaxes.Currency,
        ),
        charges=[
            ChargeDetails(
                name="base_charge",
                amount=float(shipment.TotalBaseCharge.Amount),
                currency=shipment.TotalBaseCharge.Currency,
            ),
        ]
        + [
            ChargeDetails(
                name=surcharge.SurchargeType,
                amount=float(surcharge.Amount.Amount),
                currency=surcharge.Amount.Currency,
            )
            for surcharge in shipment.Surcharges
        ]
        + [
            ChargeDetails(
                name=fee.Type,
                amount=float(fee.Amount.Amount),
                currency=fee.Amount.Currency,
            )
            for fee in shipment.AncillaryFeesAndTaxes
        ],
        documents=reduce(
            lambda labels, pkg: labels
            + [str(b64encode(part.Image), "utf-8") for part in pkg.Label.Parts],
            items,
            [],
        ),
    )


def process_shipment_request(
    payload: ShipmentRequest, settings: Settings
) -> Serializable[ProcessShipmentRequest]:
    dimension_unit = DimensionUnit[payload.parcel.dimension_unit or "IN"]
    weight_unit = WeightUnit[payload.parcel.weight_unit or "LB"]
    service = next(
        (ServiceType[s].value for s in payload.parcel.services if s in ServiceType.__members__),
        None
    )
    options = Options(payload.parcel.options)
    special_services = [
        SpecialServiceType[name].value
        for name, value in payload.parcel.options.items()
        if name in SpecialServiceType.__members__
    ]

    request = ProcessShipmentRequest(
        WebAuthenticationDetail=settings.webAuthenticationDetail,
        ClientDetail=settings.clientDetail,
        TransactionDetail=TransactionDetail(CustomerTransactionId="IE_v18_Ship"),
        Version=VersionId(ServiceId="ship", Major=25, Intermediate=0, Minor=0),
        RequestedShipment=RequestedShipment(
            ShipTimestamp=datetime.now(),
            DropoffType="REGULAR_PICKUP",
            ServiceType=service,
            PackagingType=PackagingType[payload.parcel.packaging_type or "small_box"].value,
            ManifestDetail=None,
            TotalWeight=FedexWeight(
                Units=weight_unit.value,
                Value=Weight(payload.parcel.weight, weight_unit).value,
            ),
            TotalInsuredValue=options.insurance.amount if options.insurance else None,
            PreferredCurrency=options.currency,
            ShipmentAuthorizationDetail=None,
            Shipper=Party(
                AccountNumber=settings.account_number,
                Tins=[
                    TaxpayerIdentification(TinType=None, Number=tax)
                    for tax in [
                        payload.shipper.federal_tax_id,
                        payload.shipper.state_tax_id,
                    ]
                ]
                if any([payload.shipper.federal_tax_id, payload.shipper.state_tax_id])
                else None,
                Contact=Contact(
                    ContactId=None,
                    PersonName=payload.shipper.person_name,
                    Title=None,
                    CompanyName=payload.shipper.company_name,
                    PhoneNumber=payload.shipper.phone_number,
                    PhoneExtension=None,
                    TollFreePhoneNumber=None,
                    PagerNumber=None,
                    FaxNumber=None,
                    EMailAddress=payload.shipper.email,
                )
                if any(
                    (
                        payload.shipper.company_name,
                        payload.shipper.phone_number,
                        payload.shipper.person_name,
                        payload.shipper.email,
                    )
                )
                else None,
                Address=Address(
                    StreetLines=concat_str(
                        payload.shipper.address_line_1, payload.shipper.address_line_2
                    ),
                    City=payload.shipper.city,
                    StateOrProvinceCode=payload.shipper.state_code,
                    PostalCode=payload.shipper.postal_code,
                    UrbanizationCode=None,
                    CountryCode=payload.shipper.country_code,
                    CountryName=None,
                    Residential=None,
                    GeographicCoordinates=None,
                ),
            ),
            Recipient=Party(
                AccountNumber=None,
                Tins=[
                    TaxpayerIdentification(TinType=None, Number=tax)
                    for tax in [
                        payload.recipient.federal_tax_id,
                        payload.recipient.state_tax_id,
                    ]
                ]
                if any(
                    [payload.recipient.federal_tax_id, payload.recipient.state_tax_id]
                )
                else None,
                Contact=Contact(
                    ContactId=None,
                    PersonName=payload.recipient.person_name,
                    Title=None,
                    CompanyName=payload.recipient.company_name,
                    PhoneNumber=payload.recipient.phone_number,
                    PhoneExtension=None,
                    TollFreePhoneNumber=None,
                    PagerNumber=None,
                    FaxNumber=None,
                    EMailAddress=payload.recipient.email,
                )
                if any(
                    (
                        payload.recipient.company_name,
                        payload.recipient.phone_number,
                        payload.recipient.person_name,
                        payload.recipient.email,
                    )
                )
                else None,
                Address=Address(
                    StreetLines=concat_str(
                        payload.recipient.address_line_1,
                        payload.recipient.address_line_2,
                    ),
                    City=payload.recipient.city,
                    StateOrProvinceCode=payload.recipient.state_code,
                    PostalCode=payload.recipient.postal_code,
                    UrbanizationCode=None,
                    CountryCode=payload.recipient.country_code,
                    CountryName=None,
                    Residential=None,
                    GeographicCoordinates=None,
                ),
            ),
            RecipientLocationNumber=None,
            Origin=None,
            SoldTo=None,
            ShippingChargesPayment=None,
            SpecialServicesRequested=ShipmentSpecialServicesRequested(
                SpecialServiceTypes=special_services,
                CodDetail=CodDetail(
                    CodCollectionAmount=Money(
                        Currency=options.currency or "USD",
                        Amount=options.cash_on_delivery.amount
                    ),
                    AddTransportationChargesDetail=None,
                    CollectionType=CodCollectionType.CASH,
                    CodRecipient=None,
                    FinancialInstitutionContactAndAddress=None,
                    RemitToName=None,
                    ReferenceIndicator=None,
                    ReturnTrackingId=None
                ) if options.cash_on_delivery else None,
                DeliveryOnInvoiceAcceptanceDetail=None,
                HoldAtLocationDetail=None,
                EventNotificationDetail=ShipmentEventNotificationDetail(
                    AggregationType=None,
                    PersonalMessage=None,
                    EventNotifications=[
                        ShipmentEventNotificationSpecification(
                            Role=None,
                            Events=NOTIFICATION_EVENTS,
                            NotificationDetail=NotificationDetail(
                                NotificationType="EMAIL",
                                EmailDetail=EMailDetail(
                                    EmailAddress=options.notification.email or payload.shipper.email,
                                    Name=payload.shipper.person_name
                                ),
                                Localization=Localization(
                                    LanguageCode="EN",
                                    LocaleCode=None
                                )
                            ),
                            FormatSpecification='TEXT'
                        )
                    ]
                ) if options.notification else None,
                ReturnShipmentDetail=None,
                PendingShipmentDetail=None,
                InternationalControlledExportDetail=None,
                InternationalTrafficInArmsRegulationsDetail=None,
                ShipmentDryIceDetail=None,
                HomeDeliveryPremiumDetail=None,
                EtdDetail=None,
                CustomDeliveryWindowDetail=None
            ) if options.has_content else None,
            ExpressFreightDetail=None,
            FreightShipmentDetail=None,
            DeliveryInstructions=None,
            VariableHandlingChargeDetail=None,
            CustomsClearanceDetail=None,
            PickupDetail=None,
            SmartPostDetail=None,
            BlockInsightVisibility=None,
            LabelSpecification=LabelSpecification(
                Dispositions=None,
                LabelFormatType=payload.label.format,
                ImageType=payload.label.type,
                LabelStockType="PAPER_7X4.75",
                LabelPrintingOrientation=None,
                LabelOrder=None,
                PrintedLabelOrigin=None,
                CustomerSpecifiedDetail=None,
            )
            if payload.label is not None
            else None,
            ShippingDocumentSpecification=None,
            RateRequestTypes=["LIST"]
            + ([] if "currency" not in payload.parcel.options else ["PREFERRED"]),
            EdtRequestType=None,
            MasterTrackingId=None,
            PackageCount=None,
            ConfigurationData=None,
            RequestedPackageLineItems=[
                RequestedPackageLineItem(
                    SequenceNumber=index,
                    GroupNumber=None,
                    GroupPackageCount=index,
                    VariableHandlingChargeDetail=None,
                    InsuredValue=None,
                    Weight=FedexWeight(
                        Units=weight_unit.value,
                        Value=Weight(pkg.weight, weight_unit).value,
                    ) if pkg.weight else None,
                    Dimensions=FedexDimensions(
                        Length=Dimension(pkg.length, dimension_unit).value,
                        Width=Dimension(pkg.width, dimension_unit).value,
                        Height=Dimension(pkg.height, dimension_unit).value,
                        Units=dimension_unit.value,
                    ) if any([pkg.length, pkg.width, pkg.height]) else None,
                    PhysicalPackaging=None,
                    ItemDescription=pkg.description,
                    ItemDescriptionForClearance=None,
                    CustomerReferences=None,
                    SpecialServicesRequested=None,
                    ContentRecords=None,
                )
                for index, pkg in enumerate(payload.customs.commodities, 1)
            ]
            if payload.customs is not None
            else None,
        ),
    )
    return Serializable(request, _request_serializer)


def _request_serializer(request: ProcessShipmentRequest) -> str:
    return clean_namespaces(
        export(
            create_envelope(body_content=request),
            namespacedef_='tns:Envelope xmlns:tns="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns="http://fedex.com/ws/ship/v25"',
        ),
        envelope_prefix="tns:",
        body_child_prefix="ns:",
        body_child_name="ProcessShipmentRequest",
    )
