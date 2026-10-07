include $(TOPDIR)/rules.mk

PKG_NAME:=gxmobile-scan
PKG_VERSION:=1.2.0
PKG_RELEASE:=2
PKG_LICENSE:=MIT
PKG_BUILD_DEPENDS:=golang/host
PKG_BUILD_PARALLEL:=1
PKG_USE_MIPS16:=0

GO_PKG:=gxmobile-scan
GO_PKG_BUILD_PKG:=gxmobile-scan

include $(INCLUDE_DIR)/package.mk
include $(TOPDIR)/feeds/packages/lang/golang/golang-package.mk

define Package/gxmobile-scan
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Guangxi Mobile IPTV scanner
  DEPENDS:=@TARGET_x86_64 +ca-bundle +curl +busybox
endef

define Package/gxmobile-scan/description
  Scan channel IDs through an existing HTTP gateway, preserve channel names,
  compare sources and publish an M3U playlist. Built from the recovered source.
endef

define Package/gxmobile-scan/conffiles
/etc/config/gxmobile
/etc/gxmobile/
endef

define Build/Prepare
	$(call Build/Prepare/Default)
	$(CP) ./src/. $(PKG_BUILD_DIR)/
endef

define Package/gxmobile-scan/install
	$(call GoPackage/Package/Install/Bin,$(1))
	$(CP) ./root/. $(1)/
endef

$(eval $(call GoBinPackage,gxmobile-scan))
$(eval $(call BuildPackage,gxmobile-scan))
