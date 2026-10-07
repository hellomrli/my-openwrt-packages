// gxmobile-scan: 广西移动IPTV每周扫描工具 (Linux/OpenWrt x86_64)
// CLI: 全段扫描频道号 -> 对比基线(含已去重源) -> 报告新增/减少 -> 重建去重版m3u
// API: gxmobile-scan -web 启动后台，由 LuCI 认证后管理
package main

import (
	"bufio"
	_ "embed"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	_ "time/tzdata"
)

//go:embed baseline.json
var embeddedBaseline []byte

type Source struct {
	Cid  int     `json:"cid"`
	H    int     `json:"h"`
	Kbps float64 `json:"kbps"`
	Gone bool    `json:"gone,omitempty"`
}

type Channel struct {
	Name       string   `json:"name"`
	Group      string   `json:"group"`
	LastWinner *int     `json:"lastWinner,omitempty"`
	Sources    []Source `json:"sources"`
}

type Baseline struct {
	Updated  string    `json:"updated"`
	Gateway  string    `json:"gateway"`
	Channels []Channel `json:"channels"`
}

type diffResult struct {
	Added   []int    `json:"added"`
	Gone    []string `json:"gone"`
	Revived []string `json:"revived"`
	Renamed int      `json:"renamed"`
}

type scanState struct {
	mu        sync.Mutex
	Running   bool        `json:"running"`
	Step      string      `json:"step"`
	StartedAt time.Time   `json:"startedAt"`
	LastRun   time.Time   `json:"lastRun"`
	Exists    int         `json:"exists"`
	LastDiff  *diffResult `json:"lastDiff,omitempty"`
	Err       string      `json:"err,omitempty"`
}

var (
	httpClient     = &http.Client{Timeout: 12 * time.Second}
	segClient      = &http.Client{Timeout: 40 * time.Second}
	gateway        = "http://192.168.50.250:8080"
	outDir         string
	state          scanState
	baselinePath   string
	baseMu         sync.RWMutex
	jobMu          sync.Mutex
	workers        = 4
	measureWorkers = 2
	defaultLo      = 3221225600
	defaultHi      = 3221226800
	publishDir     = "/www/gxmobile"
)

func logf(format string, a ...any) { fmt.Printf(format+"\n", a...) }

func getURL(u string, cl *http.Client) (int, []byte, string) {
	resp, err := cl.Get(u)
	if err != nil {
		return 0, nil, ""
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024*1024+1))
	if err != nil || len(body) > 64*1024*1024 {
		return 0, nil, ""
	}
	return resp.StatusCode, body, resp.Request.URL.String()
}

func probeExists(cid int) (int, int) {
	code, body, _ := getURL(fmt.Sprintf("%s/%d/index.m3u8?servicetype=1", gateway, cid), httpClient)
	if code == 200 && !strings.HasPrefix(strings.TrimSpace(string(body)), "#EXTM3U") {
		code = 0
	}
	return cid, code
}

func measureSegment(cid int) (int, float64) {
	code, body, finalURL := getURL(fmt.Sprintf("%s/%d/index.m3u8?servicetype=1", gateway, cid), httpClient)
	if code != 200 {
		return cid, 0
	}
	var last string
	duration := 0.0
	for _, line := range strings.Split(string(body), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "#EXTINF:") {
			duration, _ = strconv.ParseFloat(strings.Split(strings.TrimPrefix(line, "#EXTINF:"), ",")[0], 64)
		}
		if line != "" && !strings.HasPrefix(line, "#") {
			last = line
		}
	}
	if last == "" || duration <= 0 {
		return cid, 0
	}
	base, err1 := url.Parse(finalURL)
	ref, err2 := url.Parse(last)
	if err1 != nil || err2 != nil {
		return cid, 0
	}
	scode, data, _ := getURL(base.ResolveReference(ref).String(), segClient)
	if scode != 200 || len(data) == 0 {
		return cid, 0
	}
	return cid, float64(len(data)) * 8 / duration / 1e6
}

func resTag(h int, kbps float64) string {
	res := ""
	switch {
	case h >= 2160:
		res = "4K"
	case h >= 1080:
		res = "1080P"
	case h >= 720:
		res = "720P"
	case h > 0:
		res = "标清"
	}
	b := ""
	if kbps >= 1 {
		b = fmt.Sprintf("%.1fM", kbps)
	} else if kbps > 0 {
		b = fmt.Sprintf("%.0fK", kbps*1000)
	}
	switch {
	case res != "" && b != "":
		return fmt.Sprintf("(%s·%s)", res, b)
	case res != "":
		return "(" + res + ")"
	case b != "":
		return "(" + b + ")"
	}
	return ""
}

func scanRange(lo, hi int, step func(string)) (map[int]bool, error) {
	var mu sync.Mutex
	exists := map[int]bool{}
	uncertain, done := 0, 0
	jobs := make(chan int)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for cid := range jobs {
				_, code := probeExists(cid)
				if code != 200 && code != 404 && code != 410 {
					_, code = probeExists(cid)
				}
				mu.Lock()
				if code == 200 {
					exists[cid] = true
				} else if code != 404 && code != 410 {
					uncertain++
				}
				done++
				if step != nil && (done%50 == 0 || done == hi-lo+1) {
					step(fmt.Sprintf("探测 %d/%d，在线 %d", done, hi-lo+1, len(exists)))
				}
				mu.Unlock()
			}
		}()
	}
	for cid := lo; cid <= hi; cid++ {
		jobs <- cid
	}
	close(jobs)
	wg.Wait()
	if uncertain > 0 {
		return nil, fmt.Errorf("%d 个地址探测异常，保留原列表，请检查网关后重试", uncertain)
	}
	return exists, nil
}

func diffBaseline(b *Baseline, exSet map[int]bool, lo, hi int) diffResult {
	d := diffResult{Added: []int{}, Gone: []string{}, Revived: []string{}}
	for i := range b.Channels {
		ch := &b.Channels[i]
		for j := range ch.Sources {
			s := &ch.Sources[j]
			if exSet[s.Cid] && s.Gone {
				s.Gone = false
				d.Revived = append(d.Revived, fmt.Sprintf("%d (%s 的信号源)", s.Cid, ch.Name))
			} else if !exSet[s.Cid] && !s.Gone && s.Cid >= lo && s.Cid <= hi {
				s.Gone = true
				d.Gone = append(d.Gone, fmt.Sprintf("%d (%s 的信号源)", s.Cid, ch.Name))
			}
		}
	}
	known := map[int]bool{}
	for _, ch := range b.Channels {
		for _, s := range ch.Sources {
			known[s.Cid] = true
		}
	}
	for c := range exSet {
		if !known[c] {
			d.Added = append(d.Added, c)
		}
	}
	sort.Ints(d.Added)
	sort.Strings(d.Gone)
	sort.Strings(d.Revived)
	return d
}

func measureNew(b *Baseline, added []int) {
	var mu sync.Mutex
	sem := make(chan struct{}, measureWorkers)
	var wg sync.WaitGroup
	for _, cid := range added {
		sem <- struct{}{}
		wg.Add(1)
		go func(c int) {
			defer wg.Done()
			_, kbps := measureSegment(c)
			<-sem
			mu.Lock()
			b.Channels = append(b.Channels, Channel{
				Name:    fmt.Sprintf("未识别新频道-%d", c),
				Group:   "新发现(待命名)",
				Sources: []Source{{Cid: c, Kbps: kbps}},
			})
			mu.Unlock()
		}(cid)
	}
	wg.Wait()
}

func remeasureAll(b *Baseline, step func(string)) {
	type job struct{ ci, si, cid int }
	var jobs []job
	for i := range b.Channels {
		for j := range b.Channels[i].Sources {
			if !b.Channels[i].Sources[j].Gone {
				jobs = append(jobs, job{i, j, b.Channels[i].Sources[j].Cid})
			}
		}
	}
	var mu sync.Mutex
	sem := make(chan struct{}, measureWorkers)
	var wg sync.WaitGroup
	done := 0
	for _, jb := range jobs {
		sem <- struct{}{}
		wg.Add(1)
		go func(j job) {
			defer wg.Done()
			_, kbps := measureSegment(j.cid)
			<-sem
			mu.Lock()
			if kbps > 0 {
				b.Channels[j.ci].Sources[j.si].Kbps = kbps
			}
			done++
			if done%50 == 0 {
				logf("      测速进度 %d/%d", done, len(jobs))
				if step != nil {
					step(fmt.Sprintf("测速 %d/%d", done, len(jobs)))
				}
			}
			mu.Unlock()
		}(jb)
	}
	wg.Wait()
}

// 返回主信号被更换的频道数
func writePlaylist(b *Baseline) (int, error) {
	renamed := 0
	var lines []string
	lines = append(lines, `#EXTM3U x-tvg-url="https://epg.112114.xyz/pp.xml"`,
		"# 广西移动IPTV去重版 · 由 gxmobile-scan 自动生成", "")
	for i := range b.Channels {
		ch := &b.Channels[i]
		var live []Source
		for _, s := range ch.Sources {
			if !s.Gone {
				live = append(live, s)
			}
		}
		if len(live) == 0 {
			continue
		}
		sort.Slice(live, func(a, k int) bool {
			if live[a].H != live[k].H {
				return live[a].H > live[k].H
			}
			return live[a].Kbps > live[k].Kbps
		})
		win := live[0]
		if ch.LastWinner != nil && *ch.LastWinner != win.Cid {
			var cur *Source
			for k := range live {
				if live[k].Cid == *ch.LastWinner {
					cur = &live[k]
					break
				}
			}
			if cur != nil && cur.H == win.H && win.Kbps < cur.Kbps*1.15 {
				win = *cur
			}
		}
		if ch.LastWinner != nil && *ch.LastWinner != win.Cid {
			renamed++
		}
		w := win.Cid
		ch.LastWinner = &w
		lines = append(lines, fmt.Sprintf("#EXTINF:-1 group-title=\"%s\",%s%s", ch.Group, ch.Name, resTag(win.H, win.Kbps)),
			fmt.Sprintf("%s/%d/index.m3u8?servicetype=1", gateway, win.Cid))
		for _, s := range live {
			if s.Cid == win.Cid {
				continue
			}
			lines = append(lines, fmt.Sprintf("# %s 淘汰 %d (%s)", ch.Name, s.Cid, resTag(s.H, s.Kbps)),
				fmt.Sprintf("#%s/%d/index.m3u8?servicetype=1", gateway, s.Cid))
		}
	}
	err := atomicWrite(filepath.Join(outDir, "广西移动IPTV_去重版.m3u"), []byte(strings.Join(lines, "\n")+"\n"))
	return renamed, err
}

func publishToWWW() error {
	if publishDir == "" {
		return nil
	}
	if err := os.MkdirAll(publishDir, 0755); err != nil {
		return err
	}
	for _, name := range []string{"广西移动IPTV_去重版.m3u", "扫描报告.txt"} {
		data, err := os.ReadFile(filepath.Join(outDir, name))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		if err := atomicWrite(filepath.Join(publishDir, name), data); err != nil {
			return err
		}
	}
	return nil
}

func saveBaseline(b *Baseline, path string) error {
	b.Updated = time.Now().Format(time.RFC3339)
	b.Gateway = gateway
	data, err := json.MarshalIndent(b, "", " ")
	if err != nil {
		return err
	}
	return atomicWrite(path, data)
}

// Rename a fully written file on the same filesystem; readers never see a
// truncated playlist, and a failed write leaves the previous file intact.
func atomicWrite(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".gxmobile-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err == nil {
		err = f.Chmod(0644)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(f.Name(), path)
}

func writeReport(b *Baseline, d diffResult, exists map[int]bool, lo, hi int, start time.Time) error {
	var sb strings.Builder
	fmt.Fprintf(&sb, "广西移动IPTV扫描报告\r\n扫描时间: %s\r\n扫描范围: %d-%d\r\n平台存在: %d\r\n基线: %d 频道 / %d 信号源\r\n",
		time.Now().Format("2006-01-02 15:04"), lo, hi, len(exists), len(b.Channels), activeSrc(b))
	fmt.Fprintf(&sb, "耗时: %s\r\n\r\n新增频道 (%d):\r\n", time.Since(start).Round(time.Second), len(d.Added))
	if len(d.Added) == 0 {
		sb.WriteString("  (无)\r\n")
	}
	for _, c := range d.Added {
		if s := findSrc(b, c); s != nil {
			fmt.Fprintf(&sb, "  +%d  码率 %.1fM  [待识别]\r\n", c, s.Kbps)
		}
	}
	fmt.Fprintf(&sb, "\r\n消失信号源 (%d):\r\n", len(d.Gone))
	if len(d.Gone) == 0 {
		sb.WriteString("  (无)\r\n")
	}
	for _, g := range d.Gone {
		fmt.Fprintf(&sb, "  -%s\r\n", g)
	}
	if len(d.Revived) > 0 {
		fmt.Fprintf(&sb, "\r\n恢复信号源 (%d):\r\n", len(d.Revived))
		for _, r := range d.Revived {
			fmt.Fprintf(&sb, "  *%s\r\n", r)
		}
	}
	return atomicWrite(filepath.Join(outDir, "扫描报告.txt"), []byte("\ufeff"+sb.String()))
}

func activeSrc(b *Baseline) int {
	n := 0
	for _, ch := range b.Channels {
		for _, s := range ch.Sources {
			if !s.Gone {
				n++
			}
		}
	}
	return n
}

func findSrc(b *Baseline, cid int) *Source {
	for i := range b.Channels {
		for j := range b.Channels[i].Sources {
			if b.Channels[i].Sources[j].Cid == cid {
				return &b.Channels[i].Sources[j]
			}
		}
	}
	return nil
}

func cloneBaseline(b *Baseline) *Baseline {
	data, _ := json.Marshal(b)
	var copy Baseline
	json.Unmarshal(data, &copy)
	return &copy
}

func runScan(base *Baseline, extPath string, lo, hi int, fast bool) (diffResult, error) {
	if !jobMu.TryLock() {
		return diffResult{}, fmt.Errorf("已有任务在运行中")
	}
	defer jobMu.Unlock()
	return runScanLocked(base, extPath, lo, hi, fast)
}

// The caller holds jobMu. Scan a private copy so status remains responsive and
// failed requests cannot delete working channels or race with channel edits.
func runScanLocked(base *Baseline, extPath string, lo, hi int, fast bool) (diff diffResult, err error) {
	state.mu.Lock()
	state.Running, state.Step, state.Err = true, "开始", ""
	state.StartedAt = time.Now()
	start := state.StartedAt
	state.mu.Unlock()
	defer func() {
		state.mu.Lock()
		state.Running = false
		if err != nil {
			state.Err = err.Error()
			state.Step = "失败：" + err.Error()
		}
		state.mu.Unlock()
	}()
	unlock := acquireLock()
	if unlock == nil {
		return diff, fmt.Errorf("扫描锁被占用")
	}
	defer unlock()
	baseMu.RLock()
	work := cloneBaseline(base)
	baseMu.RUnlock()
	step := func(s string) { state.mu.Lock(); state.Step = s; state.mu.Unlock() }
	logf("[1/4] 扫描 %d~%d (经网关 %s)", lo, hi, gateway)
	exists, err := scanRange(lo, hi, step)
	if err != nil {
		return diff, err
	}
	if len(exists) == 0 {
		return diff, fmt.Errorf("没有探测到频道，保留原列表")
	}
	// A severely degraded upstream must not wipe a working subscription.
	known, live := 0, 0
	for _, ch := range work.Channels {
		for _, src := range ch.Sources {
			if src.Cid >= lo && src.Cid <= hi && !src.Gone {
				known++
				if exists[src.Cid] {
					live++
				}
			}
		}
	}
	if known >= 10 && live*2 < known {
		return diff, fmt.Errorf("超过一半的已有信号源失联，保留原列表")
	}
	step("对比基线")
	diff = diffBaseline(work, exists, lo, hi)
	step("测量码率")
	if len(diff.Added) > 0 {
		measureNew(work, diff.Added)
	}
	if !fast {
		remeasureAll(work, step)
	}
	step("保存播放列表")
	sort.SliceStable(work.Channels, func(i, j int) bool { return work.Channels[i].Name < work.Channels[j].Name })
	if diff.Renamed, err = writePlaylist(work); err != nil {
		return diff, err
	}
	if err = writeReport(work, diff, exists, lo, hi, start); err != nil {
		return diff, err
	}
	if err = saveBaseline(work, extPath); err != nil {
		return diff, err
	}
	baseMu.Lock()
	*base = *work
	baseMu.Unlock()
	if err = publishToWWW(); err != nil {
		return diff, fmt.Errorf("扫描已保存，但发布失败：%w", err)
	}
	state.mu.Lock()
	state.Exists, state.LastDiff, state.LastRun = len(exists), &diff, time.Now()
	state.Step = fmt.Sprintf("完成：新增 %d，消失 %d，恢复 %d", len(diff.Added), len(diff.Gone), len(diff.Revived))
	state.mu.Unlock()
	logf("完成：%d 个频道 / %d 个在线源", len(work.Channels), activeSrc(work))
	return diff, nil
}

func loadBaseline(extPath string) *Baseline {
	data, err := os.ReadFile(extPath)
	if os.IsNotExist(err) {
		data, err = embeddedBaseline, nil
	}
	var base Baseline
	if err != nil || json.Unmarshal(data, &base) != nil || len(base.Channels) == 0 {
		logf("基线不可读或已损坏，拒绝覆盖：%s", extPath)
		os.Exit(1)
	}
	if base.Gateway != "" {
		gateway = base.Gateway
	}
	logf("已加载基线：%d 频道 / %d 在线源", len(base.Channels), activeSrc(&base))
	return &base
}

func parseRange(value string) (int, int, error) {
	parts := strings.Split(value, "-")
	if len(parts) != 2 {
		return 0, 0, fmt.Errorf("扫描范围格式应为 起始CID-结束CID")
	}
	lo, e1 := strconv.ParseInt(parts[0], 10, 64)
	hi, e2 := strconv.ParseInt(parts[1], 10, 64)
	if e1 != nil || e2 != nil || lo < 1 || hi < lo || hi > 4294967295 || hi-lo >= 5000 {
		return 0, 0, fmt.Errorf("扫描范围必须为有效 CID，且不超过 5000 个")
	}
	return int(lo), int(hi), nil
}

func main() {
	fast := flag.Bool("fast", false, "快速模式：只测新增源的码率")
	noPause := flag.Bool("no-pause", false, "完成后直接退出")
	rangeStr := flag.String("range", "3221225600-3221226800", "CID 扫描范围")
	out := flag.String("out", "/etc/gxmobile", "持久化输出目录")
	web := flag.Bool("web", false, "启动后台 API")
	addr := flag.String("addr", "127.0.0.1:8081", "后台监听地址")
	auth := flag.String("auth", "", "独立 API 基础认证，格式 用户名:密码")
	gw := flag.String("gateway", "", "播放网关，默认沿用基线")
	flag.IntVar(&workers, "workers", 4, "探测并发数 (1-16)")
	flag.IntVar(&measureWorkers, "measure-workers", 2, "码率测量并发数 (1-4)")
	flag.StringVar(&publishDir, "publish", "/www/gxmobile", "发布目录，空字符串禁用发布")
	flag.Parse()
	fixConsole()
	var err error
	defaultLo, defaultHi, err = parseRange(*rangeStr)
	if err != nil || workers < 1 || workers > 16 || measureWorkers < 1 || measureWorkers > 4 {
		logf("无效扫描范围或并发参数：%v", err)
		os.Exit(2)
	}
	outDir = *out
	if err := os.MkdirAll(outDir, 0755); err != nil {
		logf("输出目录不可写：%v", err)
		os.Exit(1)
	}
	baselinePath = filepath.Join(outDir, "iptv_baseline.json")
	base := loadBaseline(baselinePath)
	if *gw != "" {
		gateway = strings.TrimRight(*gw, "/")
	}
	u, err := url.Parse(gateway)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		logf("无效网关地址")
		os.Exit(2)
	}
	if *web {
		if _, err := os.Stat(filepath.Join(outDir, "广西移动IPTV_去重版.m3u")); os.IsNotExist(err) {
			if _, err := writePlaylist(base); err != nil {
				logf("生成列表失败：%v", err)
				os.Exit(1)
			}
		}
		if err := publishToWWW(); err != nil {
			logf("发布列表失败：%v", err)
		}
		runWeb(*addr, *auth, base)
		return
	}
	_, err = runScan(base, baselinePath, defaultLo, defaultHi, *fast)
	if err != nil {
		logf("扫描失败：%v", err)
		waitExit(*noPause, 1)
	}
	waitExit(*noPause, 0)
}

func waitExit(noPause bool, code int) {
	if !noPause {
		fmt.Println("\n按回车键退出...")
		bufio.NewReader(os.Stdin).ReadString('\n')
	}
	os.Exit(code)
}
