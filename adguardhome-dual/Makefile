include $(TOPDIR)/rules.mk

PKG_NAME:=adguardhome-dual
PKG_VERSION:=0.107.77
PKG_RELEASE:=1

PKG_SOURCE:=AdGuardHome_linux_amd64.tar.gz
PKG_SOURCE_URL:=https://github.com/AdguardTeam/AdGuardHome/releases/download/v$(PKG_VERSION)
PKG_HASH:=9d77af61881fbcef04ba50d53fa80af16ce8dd9b02fdb4faea154b741d08c72b
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

include $(INCLUDE_DIR)/package.mk

define Package/adguardhome-dual
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Dual AdGuardHome runtime for dnsmasq + daed routing
  URL:=https://github.com/AdguardTeam/AdGuardHome
  DEPENDS:=@TARGET_x86_64 +ca-bundle
endef

define Package/adguardhome-dual/description
  Installs two AdGuardHome instances for dnsmasq + daed DNS routing:
  adh-direct on 127.0.0.1:50530 / web 192.168.50.1:50080 and
  adh-proxy on 127.0.0.1:50531 / web 192.168.50.1:50081.
endef

define Package/adguardhome-dual/conffiles
/etc/AdGuardHome-direct.yaml
/etc/AdGuardHome-proxy.yaml
endef

define Build/Prepare
	$(RM) -r $(PKG_BUILD_DIR)
	$(MKDIR) $(PKG_BUILD_DIR)
	$(HOST_TAR) -C $(PKG_BUILD_DIR) --strip-components=1 -xzf $(DL_DIR)/$(PKG_SOURCE)
endef

define Build/Compile
endef

define Package/adguardhome-dual/install
	$(INSTALL_DIR) $(1)/usr/bin/AdGuardHome
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/AdGuardHome $(1)/usr/bin/AdGuardHome/AdGuardHome-direct
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/AdGuardHome $(1)/usr/bin/AdGuardHome/AdGuardHome-proxy
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/etc/init.d/adh-direct $(1)/etc/init.d/adh-direct
	$(INSTALL_BIN) ./files/etc/init.d/adh-proxy $(1)/etc/init.d/adh-proxy
	$(INSTALL_DIR) $(1)/etc
	$(INSTALL_CONF) ./files/etc/AdGuardHome-direct.yaml $(1)/etc/AdGuardHome-direct.yaml
	$(INSTALL_CONF) ./files/etc/AdGuardHome-proxy.yaml $(1)/etc/AdGuardHome-proxy.yaml
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/etc/uci-defaults/99-adguardhome-dual $(1)/etc/uci-defaults/99-adguardhome-dual
endef

$(eval $(call BuildPackage,adguardhome-dual))
