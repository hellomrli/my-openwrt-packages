'use strict';
'require view';
'require rpc';
'require ui';
'require poll';
'require dom';

var callStatus = rpc.declare({ object: 'luci.gxmobile', method: 'status', expect: { '': {} } });
var callChannels = rpc.declare({ object: 'luci.gxmobile', method: 'channels', expect: { '': {} } });
var callReport = rpc.declare({ object: 'luci.gxmobile', method: 'report', expect: { '': {} } });
var callSettings = rpc.declare({ object: 'luci.gxmobile', method: 'settings', expect: { '': {} } });
var callScan = rpc.declare({ object: 'luci.gxmobile', method: 'scan', params: ['mode', 'range'], expect: { '': {} } });
var callRename = rpc.declare({ object: 'luci.gxmobile', method: 'rename', params: ['name', 'newName', 'group'], expect: { '': {} } });
var callDelete = rpc.declare({ object: 'luci.gxmobile', method: 'delete', params: ['name'], expect: { '': {} } });
var settingKeys = ['enabled', 'schedule_enabled', 'frequency', 'weekday', 'hour', 'minute', 'mode', 'gateway', 'range', 'workers', 'measure_workers'];
var callConfigure = rpc.declare({ object: 'luci.gxmobile', method: 'configure', params: settingKeys, expect: { '': {} } });

function checked(result) {
	if (result.error) throw new Error(result.error);
	return result;
}

function notify(error) { ui.addNotification(null, E('p', {}, error.message || String(error)), 'error'); }

return view.extend({
	load: function() {
		return Promise.all([callSettings(), callStatus(), callChannels(), callReport()]);
	},

	refresh: function() {
		return callStatus().then(function(status) {
			this.status = status;
			this.updateStatus();
			// Channel lists are stable while a scan runs. Refresh on completion or edits.
			if (!status.running && !status.error)
				return Promise.all([callChannels(), callReport()]).then(function(data) {
					this.channels = checked(data[0]).channels || [];
					this.report.textContent = checked(data[1]).report || '尚无扫描报告';
					this.updateChannels();
				}.bind(this));
		}.bind(this)).catch(function(error) {
			this.status = { error: error.message || String(error) };
			this.updateStatus();
		}.bind(this));
	},

	updateStatus: function() {
		var s = this.status || {}, diff = s.lastDiff || {};
		this.info.textContent = s.error || s.err || s.step || '空闲，等待扫描';
		this.info.style.color = (s.error || s.err) ? '#c33' : '';
		this.cards.state.textContent = s.error ? '后台离线' : s.running ? '扫描中' : '空闲';
		this.cards.channels.textContent = s.channels == null ? '—' : s.channels;
		this.cards.sources.textContent = s.sources == null ? '—' : s.sources;
		this.cards.diff.textContent = '+' + (diff.added || []).length + ' / −' + (diff.gone || []).length;
		this.cards.updated.textContent = s.lastRun && !s.lastRun.startsWith('0001-')
			? new Date(s.lastRun).toLocaleString() : s.updated || '尚未扫描';
		this.fast.disabled = this.full.disabled = !this.writable || !!s.running || !!s.error;
		this.save.disabled = !this.writable || !!s.running;
		Object.keys(this.inputs).forEach(function(key) { this.inputs[key].disabled = !this.writable || !!s.running; }, this);
	},

	scan: function(mode) {
		this.fast.disabled = this.full.disabled = true;
		return callScan(mode, '').then(checked).then(function() {
			this.status.running = true;
			this.status.step = '扫描已启动';
			this.updateStatus();
			return this.refresh();
		}.bind(this)).catch(function(error) { notify(error); this.updateStatus(); }.bind(this));
	},

	edit: function(channel) {
		var name = E('input', { 'class': 'cbi-input-text', 'value': channel.name, 'maxlength': 200 });
		var group = E('input', { 'class': 'cbi-input-text', 'value': channel.group, 'maxlength': 200 });
		ui.showModal('编辑频道', [
			E('p', {}, ['频道名称', E('br'), name]), E('p', {}, ['分组', E('br'), group]),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn', 'click': ui.hideModal }, '取消'), ' ',
				E('button', { 'class': 'btn cbi-button-positive', 'click': ui.createHandlerFn(this, function() {
					return callRename(channel.name, name.value.trim(), group.value.trim()).then(checked).then(function() {
						ui.hideModal(); return this.refresh();
					}.bind(this)).catch(notify);
				}) }, '保存')
			])
		]);
	},

	remove: function(channel) {
		ui.showModal('删除频道', [
			E('p', {}, '删除「' + channel.name + '」？下次扫描发现相同 CID 时，会作为新频道重新加入。'),
			E('div', { 'class': 'right' }, [
				E('button', { 'class': 'btn', 'click': ui.hideModal }, '取消'), ' ',
				E('button', { 'class': 'btn cbi-button-negative', 'click': ui.createHandlerFn(this, function() {
					return callDelete(channel.name).then(checked).then(function() { ui.hideModal(); return this.refresh(); }.bind(this)).catch(notify);
				}) }, '删除')
			])
		]);
	},

	updateChannels: function() {
		var search = this.search.value.trim().toLowerCase(), group = this.group.value;
		var groups = Array.from(new Set(this.channels.map(function(c) { return c.group; }))).sort();
		dom.content(this.group, [E('option', { value: '' }, '全部分组')].concat(groups.map(function(g) { return E('option', { value: g }, g); })));
		this.group.value = groups.indexOf(group) >= 0 ? group : '';
		var rows = this.channels.filter(function(c) {
			return (!group || c.group == group) && (!search || c.name.toLowerCase().indexOf(search) >= 0 || String(c.winner).indexOf(search) >= 0);
		}).map(function(c) {
			var link = /^https?:\/\//.test(c.url || '') ? E('a', { href: c.url, target: '_blank', rel: 'noopener noreferrer' }, String(c.winner)) : '—';
			return [c.name, c.group, link, c.tag || '—', c.live + ' / ' + c.gone,
				E('div', { style: 'white-space:nowrap' }, [
					E('button', { 'class': 'btn cbi-button-edit', disabled: !this.writable || !!this.status.running, click: this.edit.bind(this, c) }, '编辑'), ' ',
					E('button', { 'class': 'btn cbi-button-remove', disabled: !this.writable || !!this.status.running, click: this.remove.bind(this, c) }, '删除')
				])];
		}, this);
		cbi_update_table(this.table, rows, E('em', {}, '没有匹配的频道'));
	},

	configure: function() {
		var args = settingKeys.map(function(key) {
			var input = this.inputs[key];
			if (input.type == 'checkbox') return input.checked;
			if (['weekday', 'hour', 'minute', 'workers', 'measure_workers'].indexOf(key) >= 0) return Number(input.value);
			return input.value.trim();
		}, this);
		if (!settingKeys.every(function(key) { return this.inputs[key].reportValidity(); }, this)) return;
		return callConfigure.apply(null, args).then(checked).then(function() {
			ui.addNotification(null, E('p', {}, '设置已保存，定时任务已更新。'), 'info');
			return this.refresh();
		}.bind(this)).catch(notify);
	},

	render: function(data) {
		this.writable = L.hasViewPermission();
		this.status = data[1];
		this.channels = data[2].channels || [];
		this.inputs = {};
		this.cards = {};
		var settings = data[0], fields = [];
		var field = function(key, title, type, values, hint) {
			var input;
			if (type == 'select') input = E('select', { 'class': 'cbi-input-select' }, values.map(function(v) { return E('option', { value: v[0] }, v[1]); }));
			else input = E('input', { 'class': type == 'checkbox' ? 'cbi-input-checkbox' : 'cbi-input-text', type: type });
			if (type == 'checkbox') input.checked = settings[key] == '1';
			else input.value = settings[key] || '';
			if (type == 'number') { input.min = values[0]; input.max = values[1]; input.step = 1; }
			if (type != 'checkbox') input.required = true;
			this.inputs[key] = input;
			fields.push(E('div', { 'class': 'cbi-value' }, [E('label', { 'class': 'cbi-value-title' }, title),
				E('div', { 'class': 'cbi-value-field' }, [input, hint ? E('div', { 'class': 'cbi-value-description' }, hint) : ''])]));
		}.bind(this);
		field('enabled', '启用扫描后台', 'checkbox');
		field('schedule_enabled', '启用定时扫描', 'checkbox');
		field('frequency', '扫描频率', 'select', [['weekly', '每周'], ['daily', '每天']]);
		field('weekday', '星期（每周模式）', 'select', [[1, '周一'], [2, '周二'], [3, '周三'], [4, '周四'], [5, '周五'], [6, '周六'], [0, '周日']]);
		field('hour', '小时', 'number', [0, 23], '使用路由器系统时区；建议安排在无人观看的时段。');
		field('minute', '分钟', 'number', [0, 59]);
		field('mode', '定时扫描模式', 'select', [['fast', '快速：探测全部 CID，只测量新增源'], ['full', '全量：同时重测所有在线源码率']]);
		field('gateway', 'HTTP 播放网关', 'url', null, '填写现有 Lucky 网关地址，必须能从本路由器访问。');
		field('range', 'CID 扫描范围', 'text', null, '格式：3221225600-3221226800，最多 5000 个 CID。');
		field('workers', '探测并发数', 'number', [1, 16]);
		field('measure_workers', '码率测量并发数', 'number', [1, 4], '默认 2；并发越高，对播放带宽的占用越大。');
		this.save = E('button', { 'class': 'btn cbi-button-save', click: ui.createHandlerFn(this, this.configure) }, '保存并应用');
		this.fast = E('button', { 'class': 'btn cbi-button-action important', click: ui.createHandlerFn(this, this.scan, 'fast') }, '快速扫描');
		this.full = E('button', { 'class': 'btn cbi-button-action', click: ui.createHandlerFn(this, this.scan, 'full') }, '全量扫描');
		this.info = E('p');
		this.search = E('input', { 'class': 'cbi-input-text', placeholder: '搜索频道名称或 CID', input: this.updateChannels.bind(this) });
		this.group = E('select', { 'class': 'cbi-input-select', change: this.updateChannels.bind(this) });
		this.table = E('table', { 'class': 'table cbi-section-table' }, [E('tr', { 'class': 'tr table-titles' },
			['频道名称', '分组', '主信号 CID', '画质 / 码率', '在线 / 失效源', '操作'].map(function(title) { return E('th', { 'class': 'th' }, title); }))]);
		this.report = E('pre', { style: 'white-space:pre-wrap;max-height:24em;overflow:auto' }, data[3].report || '尚无扫描报告');
		var playlist = window.location.origin + '/gxmobile/' + encodeURIComponent('广西移动IPTV_去重版.m3u');
		var cards = [['state', '任务状态'], ['channels', '频道数'], ['sources', '在线信号源'], ['diff', '新增 / 消失'], ['updated', '最近更新']].map(function(item) {
			this.cards[item[0]] = E('strong', { style: 'display:block;font-size:1.2em;margin-top:.4em' }, '—');
			return E('div', { 'class': 'cbi-section', style: 'padding:1em;margin:0' }, [E('span', {}, item[1]), this.cards[item[0]]]);
		}, this);
		var node = E('div', {}, [
			E('h2', {}, 'IPTV 扫描'),
			E('p', {}, '发现新频道、检查失效源，保留频道命名并更新去重播放列表。全量扫描会读取视频片段，产生额外流量。'),
			E('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1em' }, cards),
			this.info,
			E('p', {}, [this.fast, ' ', this.full, ' ', E('a', { 'class': 'btn', href: playlist, download: '广西移动IPTV_去重版.m3u' }, '下载 M3U')]),
			E('p', {}, ['播放器订阅地址：', E('input', { 'class': 'cbi-input-text', readonly: true, value: playlist, style: 'width:100%', click: function(ev) { ev.target.select(); } })]),
			E('details', { 'class': 'cbi-section' }, [E('summary', { style: 'cursor:pointer;padding:1em' }, '定时扫描与扫描设置'), E('div', {}, fields), E('p', { 'class': 'right' }, this.save)]),
			E('h3', {}, '频道列表'), E('p', {}, [this.search, ' ', this.group]),
			E('div', { style: 'overflow-x:auto' }, this.table),
			E('details', { 'class': 'cbi-section' }, [E('summary', { style: 'cursor:pointer;padding:1em' }, '最近扫描报告'), this.report])
		]);
		this.updateStatus();
		this.updateChannels();
		poll.add(this.refresh.bind(this), 5);
		return node;
	},
	handleSave: null,
	handleSaveApply: null,
	handleReset: null
});
