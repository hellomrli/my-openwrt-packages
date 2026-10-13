const workflowUrl = 'https://api.github.com/repos/kenzok8/openwrt-daede/actions/workflows/auto-bump.yml/dispatches';
const lanes = {
  '17 */6 * * *': 'core',
  '47 19 * * 0': 'perf',
};

export default {
  async scheduled(controller, env) {
    const lane = lanes[controller.cron];
    if (!lane) throw new Error(`Unexpected cron: ${controller.cron}`);
    if (!env.GITHUB_DISPATCH_TOKEN) throw new Error('Missing GITHUB_DISPATCH_TOKEN');

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const response = await fetch(workflowUrl, {
          method: 'POST',
          headers: {
            Accept: 'application/vnd.github+json',
            Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
            'Content-Type': 'application/json',
            'User-Agent': 'daede-upstream-scheduler',
            'X-GitHub-Api-Version': '2026-03-10',
          },
          body: JSON.stringify({ ref: 'main', inputs: { lane, unattended: true } }),
        });
        if (response.status >= 200 && response.status < 300) {
          console.log(`Dispatched ${lane} upstream sync`);
          return;
        }
        if (response.status !== 429 && response.status < 500) {
          throw new Error(`GitHub dispatch rejected: HTTP ${response.status}`);
        }
        if (attempt === 3) throw new Error(`GitHub dispatch failed: HTTP ${response.status}`);
      } catch (error) {
        if (attempt === 3 || error.message.startsWith('GitHub dispatch rejected:')) throw error;
      }
      await new Promise(resolve => setTimeout(resolve, attempt * 1000));
    }
  },
};
