include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-iptv-catchup
PKG_VERSION:=0.1.2
PKG_RELEASE:=1
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-iptv-catchup
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=Guangxi Mobile IPTV catchup and timeshift
  DEPENDS:=+luci-base +rpcd +python3
  URL:=https://github.com/hellomrli/my-openwrt-packages
endef

define Package/luci-app-iptv-catchup/description
  LAN-only catchup and timeshift HLS translation with UCI configuration,
  procd supervision and dynamically generated IPTV subscriptions.
endef

define Package/luci-app-iptv-catchup/conffiles
/etc/config/iptv-catchup
/etc/iptv-catchup/IPTV.m3u
/etc/iptv-catchup/catchup.json
endef

define Build/Prepare
	$(call Build/Prepare/Default)
	test -f ./root/usr/lib/iptv-catchup/openwrt_service.py
endef

define Build/Compile
endef

define Package/luci-app-iptv-catchup/install
	$(CP) ./root/. $(1)/
endef

define Package/luci-app-iptv-catchup/postinst
#!/bin/sh
if [ -z "$$IPKG_INSTROOT" ]; then
	/etc/init.d/iptv-catchup enable
	/etc/init.d/rpcd reload
	rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
	if /etc/init.d/iptv-catchup running; then
		/etc/init.d/iptv-catchup restart
	elif [ "$$(uci -q get iptv-catchup.main.enabled)" = "1" ]; then
		/etc/init.d/iptv-catchup start
	fi
fi
exit 0
endef

define Package/luci-app-iptv-catchup/prerm
#!/bin/sh
if [ -z "$$IPKG_INSTROOT" ]; then
	/etc/init.d/iptv-catchup stop
	/etc/init.d/iptv-catchup disable
fi
exit 0
endef

$(eval $(call BuildPackage,luci-app-iptv-catchup))
