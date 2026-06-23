SITE_CATEGORIES = [
    "Shopify",
    "BigCommerce",
    "WooCommerce",
    "Wix eCommerce",
    "Squarespace Commerce",
    "Magento / Adobe Commerce",
    "PrestaShop",
    "OpenCart",
    "Shoplazza",
    "Shopline",
    "Funpinpin",
    "Ueeshop / SHOPYY / XShoppy",
    "Other",
]


def normalize_site_category(category: str) -> str:
    if category in SITE_CATEGORIES:
        return category
    return "Other"
