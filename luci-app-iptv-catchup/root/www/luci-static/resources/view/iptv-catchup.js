'use strict';
'require view';
'require form';
'require rpc';
'require uci';
'require poll';
'require dom';

var callService = rpc.declare({
	object: 'service', method: 'list', params: ['name'], expect: { '': {} }
});
var callLAN = rpc.declare({
	object: 'network.interface.lan', method: 'status', expect: { '': {} }
});

function validURL(value) {
	try {
		var url = new URL(value);
		return /^https?:$/.test(url.protocol) && !!url.hostname &&
			!url.username && !url.password && !url.search && !url.hash &&
			!/[\s"<>{}]/.test(value);
	}
	catch (e) { return false; }
}

return view.extend({
	load: function() {
		return Promise.all([uci.load('iptv-catchup'), callService('iptv-catchup'), callLAN()]);
	},

	updateStatus: function(service, lan) {
		var instance = ((service['iptv-catchup'] || {}).instances || {}).main || {};
		this.state.textContent = instance.running ? '运行中，PID ' + instance.pid :
			(uci.get('iptv-catchup', 'main', 'enabled') == '1' ? '未运行，请检查系统日志' : '已停用');
		this.state.style.color = instance.running ? '#287b36' : '#a33';
		var host = uci.get('iptv-catchup', 'main', 'listen_address') ||
			((lan['ipv4-address'] || [])[0] || {}).address;
		var base = uci.get('iptv-catchup', 'main', 'base_url') ||
			(host && host != '0.0.0.0' ? 'http://' + host + ':' +
				(uci.get('iptv-catchup', 'main', 'port') || '8088') : '');
		if (!base || !validURL(base)) {
			dom.content(this.links, 'LAN 地址尚未就绪，或需要设置播放器访问地址。');
			return;
		}
		base = base.replace(/\/+$/, '');
		dom.content(this.links, [
			E('p', {}, ['酷9 / 回放订阅：', E('a', { href: base + '/IPTV-catchup.m3u' }, base + '/IPTV-catchup.m3u')]),
			E('p', {}, ['纯直播订阅：', E('a', { href: base + '/IPTV.m3u' }, base + '/IPTV.m3u')]),
			E('p', {}, ['运营商直连（需 IPTV 网络）：', E('a', { href: base + '/gx.m3u' }, base + '/gx.m3u')]),
			E('p', {}, [E('a', { href: base + '/status', target: '_blank', rel: 'noopener noreferrer' }, '服务健康状态')])
		]);
	},

	render: function(data) {
		var m = new form.Map('iptv-catchup', 'IPTV 回放与时移',
			'将广西移动历史 TS 转换为 HLS，不录制、不缓存视频。需要现有直播网关可用。' +
			'服务无认证，仅限可信 LAN；不要映射到公网。单次回放最多 4 小时，时移窗口最多 4 小时。');
		var s = m.section(form.NamedSection, 'main', 'service');
		s.addremove = false;
		var o = s.option(form.Flag, 'enabled', '启用服务');
		o.rmempty = false;
		o = s.option(form.Value, 'gateway', '直播网关');
		o.placeholder = 'http://192.168.50.250:8080';
		o.rmempty = false;
		o.description = '必须能由路由器访问；频道直播地址也会改写为此网关。播放器须能访问其返回的 TS 地址。';
		o.validate = function(section, value) {
			return validURL(value) || '请输入 HTTP(S) 地址，不含账号、查询参数或片段';
		};
		o = s.option(form.Value, 'listen_address', '监听 IPv4');
		o.placeholder = '自动使用 LAN IPv4';
		o.datatype = 'ip4addr("nomask")';
		o.description = '留空时跟随 LAN 地址；通常无需修改。不建议使用 0.0.0.0。';
		o = s.option(form.Value, 'port', '监听端口');
		o.datatype = 'range(1024,65535)';
		o.default = '8088';
		o.rmempty = false;
		o = s.option(form.Value, 'base_url', '播放器访问地址');
		o.placeholder = '自动使用 http://LAN地址:端口';
		o.description = '仅反向代理或手动监听全部接口时填写，可包含代理路径前缀。HTTPS LuCI 中的 HTTP 链接可能被浏览器拦截，请复制到播放器。';
		o.validate = function(section, value) {
			if (!value && this.map.lookupOption('listen_address', section)[0].formvalue(section) == '0.0.0.0')
				return '监听全部接口时必须指定播放器能访问的地址';
			return !value || validURL(value) || '请输入 HTTP(S) 地址，不含账号、查询参数或片段';
		};
		o = s.option(form.ListValue, 'format', '回放时间占位符');
		o.value('ku9', '酷9（北京时间，yyyyMMddHHmmss）');
		o.value('unix', '通用（Unix 秒，{utc} / {utcend}）');
		o.default = 'ku9';
		o.rmempty = false;
		o = s.option(form.ListValue, 'access_log', '诊断日志');
		o.value('errors', '仅失败和慢请求（推荐）');
		o.value('all', '全部请求（临时排障）');
		o.value('off', '关闭日志（保留内存统计）');
		o.default = 'errors';
		o.rmempty = false;
		o.description = '记录请求 ID、耗时、探测和纠偏信息，不记录完整 URL、会话令牌或客户端 IP。' +
			'状态接口保留最近各 20 条失败及耗时达到 2 秒的请求；重启后清空。全部日志建议只在复现卡顿时短期开启。';
		this.state = E('strong');
		this.links = E('div');
		var status = E('div', { 'class': 'cbi-section' }, [
			E('h3', {}, '运行状态与订阅'), E('p', {}, this.state), this.links,
			E('p', {}, '订阅链接以已保存配置为准。应用设置后会自动重启服务；更换端口后需重新导入订阅。'),
			E('p', {}, '频道表：/etc/iptv-catchup/IPTV.m3u；回放白名单及天数：/etc/iptv-catchup/catchup.json。' +
				'内置天数不代表运营商保证可用；节目单由播放器读取 M3U 中的 EPG 地址。')
		]);
		this.updateStatus(data[1], data[2]);
		poll.add(function() {
			return Promise.all([callService('iptv-catchup'), callLAN()]).then(function(results) {
				this.updateStatus(results[0], results[1]);
			}.bind(this));
		}.bind(this), 5);
		return m.render().then(function(node) { return E('div', {}, [status, node]); });
	}
});
