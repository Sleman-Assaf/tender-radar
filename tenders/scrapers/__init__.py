# tenders/scrapers/__init__.py
"""
Scraper registry — maps source scraper_class names to actual classes.
Add new scrapers here after creating them.
"""

from .jordan_gtd   import JordanGTDScraper
from .joneps       import JONEPSScraper
from .adfd         import ADFDScraper
from .adb          import ADBScraper
from .badea        import BADEAScraper
from .uae_business_gate import UAEBusinessGateScraper
from .ashghal      import AshghalScraper
from .aseza        import ASEZAScraper
from .bahrain_tb   import BahrainTBScraper
from .kenha        import KeNHAScraper
from .globaltenders import GlobalTendersScraper
from .tenderjo      import TenderJOScraper
from .iqtenders     import IQTendersScraper
from .tendersa        import TenderSAScraper
from .sytenders       import SyTendersScraper
from .elan_sy         import ElanSYScraper
from .tenderuae       import TenderUAEScraper
from .syria_scrapers  import SyriaTenderScraper
from .africagateway   import AfricaGatewayScraper
from .developmentaid  import DevelopmentAidScraper, DevelopmentAidRwandaScraper
from .itp_iq        import ITPIQScraper
from .saudi_etimad import SaudiEtimadScraper
from .saudi_etimad_qual import SaudiEtimadQualScraper
from .saudi_etimad_supplier_templates import SaudiEtimadSupplierTemplatesScraper
from .saudi_etimad_preplanning         import SaudiEtimadPrePlanningScraper
from .ncp_saudi                        import NCPSaudiScraper
from .gtai_kfw_jordan                  import GTAIKfwJordanScraper
from .badea                            import BADEAScraper
from .afdb_consultants                 import AfDBConsultantsScraper
from .ec_funding_tenders               import ECFundingTendersScraper
from .undp_jordan                      import UNDPJordanScraper
from .mca_jordan                       import MCAJordanScraper
from .tendersinfo_jordan               import TendersInfoJordanScraper
from .gam_tenders                      import GAMTendersScraper
from .tenderwell                       import TenderWellScraper
from .giz_tenders                      import GIZTendersScraper
from .dtvp_giz                         import DTVPGIZScraper
from .nepco_tenders                    import NEPCOTendersScraper
from .eib_procurement                  import EIBProcurementScraper
from .adc_tenders                      import ADCTendersScraper
from .dgmarket                         import DGMarketScraper
from .unido_procurement                import UNIDOProcurementScraper
from .ungm                             import UNGMScraper
from .mola_tenders                     import MOLATendersScraper
from .memr_tenders                     import MEMRTendersScraper
from .pdtra_tenders                    import PDTRATendersScraper
from .jpmc_tenders                     import JPMCTendersScraper
from .apc_tenders                      import APCTendersScraper
from .afd_dgmarket                     import AFDDGMarketScraper
from .uae_portals  import UAEAbuDhabiScraper, UAEDubaiTendersScraper
from .egpu_uganda  import EGPUUgandaScraper
from .dewa         import DEWAScraper
from .esupply_dubai   import ESupplyDubaiScraper
from .umucyo_rwanda   import UmucyoRwandaScraper
from .mof_uae      import MOFUAEScraper
from .gulf_portals import OmanTenderBoardScraper, QatarTendersScraper
from .moroccotenders import MoroccoTendersScraper
from .worldbank     import WorldBankScraper
from .worldbank_rfx import WorldBankRFxScraper
from .gtai_kfw     import GTAIKfWScraper
from .ungm         import UNGMScraper
from .undp         import UNDPScraper
from .isdb         import IsDBScraper
from .ebrd         import EBRDScraper
from .rwanda       import RwandaUMUCYOScraper
from .afd          import AFDScraper
from .afdb         import AfDBScraper
from .iraq_businessnews import IraqBusinessNewsScraper
from .kuwait_capt      import KuwaitCAPTScraper
from .kuwait_pahw      import KuwaitPAHWScraper
from .tenderimpulse    import TenderImpulseMiddleEastScraper, TenderImpulseAfricaScraper
from .biddetail        import BidDetailWesternAsiaScraper, BidDetailEasternAfricaScraper
from .era_ethiopia     import ERAEthiopiaScraper
from .wasac_rwanda     import WASACRwandaScraper

SCRAPER_REGISTRY: dict[str, type] = {
    'JordanGTDScraper':          JordanGTDScraper,
    'JONEPSScraper':             JONEPSScraper,
    'ADFDScraper':               ADFDScraper,
    'ADBScraper':                ADBScraper,
    'BADEAScraper':              BADEAScraper,
    'UAEBusinessGateScraper':    UAEBusinessGateScraper,
    'AshghalScraper':            AshghalScraper,
    'ASEZAScraper':              ASEZAScraper,
    'BahrainTBScraper':          BahrainTBScraper,
    'KeNHAScraper':              KeNHAScraper,
    'GlobalTendersScraper':      GlobalTendersScraper,
    'TenderJOScraper':           TenderJOScraper,
    'IQTendersScraper':          IQTendersScraper,
    'TenderSAScraper':           TenderSAScraper,
    'SyTendersScraper':          SyTendersScraper,
    'ElanSYScraper':             ElanSYScraper,
    'TenderUAEScraper':          TenderUAEScraper,
    'SyriaTenderScraper':        SyriaTenderScraper,
    # Alias — some ScraperSource rows were created with this name for the
    # SyriaTender.com / your-tender.com portal; same implementation.
    'SyriaTenderComScraper':     SyriaTenderScraper,
    'AfricaGatewayScraper':      AfricaGatewayScraper,
    'DevelopmentAidScraper':     DevelopmentAidScraper,
    'DevelopmentAidRwandaScraper': DevelopmentAidRwandaScraper,
    'ITPIQScraper':              ITPIQScraper,
    'SaudiEtimadScraper':        SaudiEtimadScraper,
    'SaudiEtimadQualScraper':    SaudiEtimadQualScraper,
    'SaudiEtimadSupplierTemplatesScraper': SaudiEtimadSupplierTemplatesScraper,
    'SaudiEtimadPrePlanningScraper':        SaudiEtimadPrePlanningScraper,
    'NCPSaudiScraper':                      NCPSaudiScraper,
    'GTAIKfwJordanScraper':                 GTAIKfwJordanScraper,
    'BADEAScraper':                          BADEAScraper,
    'AfDBConsultantsScraper':                AfDBConsultantsScraper,
    'ECFundingTendersScraper':               ECFundingTendersScraper,
    'UNDPJordanScraper':                     UNDPJordanScraper,
    'MCAJordanScraper':                      MCAJordanScraper,
    'TendersInfoJordanScraper':               TendersInfoJordanScraper,
    'GAMTendersScraper':                      GAMTendersScraper,
    'TenderWellScraper':                      TenderWellScraper,
    'GIZTendersScraper':                      GIZTendersScraper,
    'DTVPGIZScraper':                         DTVPGIZScraper,
    'NEPCOTendersScraper':                    NEPCOTendersScraper,
    'EIBProcurementScraper':                  EIBProcurementScraper,
    'ADCTendersScraper':                      ADCTendersScraper,
    'DGMarketScraper':                        DGMarketScraper,
    'UNIDOProcurementScraper':                UNIDOProcurementScraper,
    'UNGMScraper':                            UNGMScraper,
    'MOLATendersScraper':                     MOLATendersScraper,
    'MEMRTendersScraper':                     MEMRTendersScraper,
    'PDTRATendersScraper':                    PDTRATendersScraper,
    'JPMCTendersScraper':                     JPMCTendersScraper,
    'APCTendersScraper':                      APCTendersScraper,
    'AFDDGMarketScraper':                     AFDDGMarketScraper,
    'UAEAbuDhabiScraper':        UAEAbuDhabiScraper,
    'EGPUUgandaScraper':         EGPUUgandaScraper,
    'UAEDubaiTendersScraper':    UAEDubaiTendersScraper,
    'DEWAScraper':               DEWAScraper,
    'ESupplyDubaiScraper':        ESupplyDubaiScraper,
    'UmucyoRwandaScraper':        UmucyoRwandaScraper,
    'MOFUAEScraper':             MOFUAEScraper,
    'OmanTenderBoardScraper':    OmanTenderBoardScraper,
    'QatarTendersScraper':       QatarTendersScraper,
    'MoroccoTendersScraper':     MoroccoTendersScraper,
    'WorldBankScraper':          WorldBankScraper,
    'WorldBankRFxScraper':       WorldBankRFxScraper,
    'GTAIKfWScraper':            GTAIKfWScraper,
    'UNGMScraper':               UNGMScraper,
    'UNDPScraper':               UNDPScraper,
    'IsDBScraper':               IsDBScraper,
    'EBRDScraper':              EBRDScraper,
    'RwandaUMUCYOScraper':      RwandaUMUCYOScraper,
    'AFDScraper':           AFDScraper,
    'AfDBScraper':          AfDBScraper,
    'IraqBusinessNewsScraper': IraqBusinessNewsScraper,
    'KuwaitCAPTScraper':      KuwaitCAPTScraper,
    'KuwaitPAHWScraper':                 KuwaitPAHWScraper,
    'TenderImpulseMiddleEastScraper':    TenderImpulseMiddleEastScraper,
    'TenderImpulseAfricaScraper':        TenderImpulseAfricaScraper,
    'BidDetailWesternAsiaScraper':      BidDetailWesternAsiaScraper,
    'BidDetailEasternAfricaScraper':     BidDetailEasternAfricaScraper,
    'ERAEthiopiaScraper':                ERAEthiopiaScraper,
    'WASACRwandaScraper':               WASACRwandaScraper,
}


def get_scraper(class_name: str):
    """Instantiate a scraper by its class name string."""
    cls = SCRAPER_REGISTRY.get(class_name)
    if cls is None:
        raise ValueError(f'Unknown scraper class: {class_name}')
    return cls()
