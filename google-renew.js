/**
 * ACLClouds Google OAuth 自动续期脚本
 * 使用 Playwright 模拟浏览器登录，获取 Cookie 后调用 API 续期
 */

const { chromium } = require('playwright');
const axios = require('axios');

// ==================== 配置 ====================
const BASE_URL = 'https://dash.aclclouds.com';
const RENEW_THRESHOLD_HOURS = parseInt(process.env.RENEW_THRESHOLD_HOURS || '48');

const GOOGLE_EMAIL = process.env.ACL_GOOGLE_EMAIL;
const GOOGLE_PASSWORD = process.env.ACL_GOOGLE_PASSWORD;

const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN || '';
const TG_CHAT_ID = process.env.TG_CHAT_ID || '';

// ==================== 工具函数 ====================
function log(msg) {
    console.log(msg, flush: true);
}

async function sendTelegram(text) {
    if (!TG_BOT_TOKEN || !TG_CHAT_ID) return;
    try {
        await axios.post(
            `https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`,
            { chat_id: TG_CHAT_ID, text, parse_mode: 'Markdown' },
            { timeout: 15000 }
        );
    } catch (e) {
        log(`⚠️ Telegram 推送失败: ${e.message}`);
    }
}

function fmtRemaining(seconds) {
    if (seconds === null || seconds === undefined) return '?';
    seconds = Math.floor(seconds);
    if (seconds < 0) return '已过期';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}天${h}小时${m}分钟`;
    if (h > 0) return `${h}小时${m}分钟`;
    return `${m}分钟`;
}

// ==================== Google 登录 ====================
async function loginWithGoogle(page) {
    log('🔑 正在通过 Google 登录...');
    
    // 访问登录页
    await page.goto(`${BASE_URL}/login`, { waitUntil: 'networkidle' });
    
    // 点击 Google 登录按钮
    await page.click('button:has-text("Google")', { timeout: 10000 }).catch(() => {
        // 如果没找到 Google 按钮，尝试其他方式
        log('⚠️ 未找到 Google 登录按钮，尝试查找 OAuth 链接...');
    });
    
    // 等待 Google 登录页面加载
    await page.waitForURL(/google|oauth|accounts\.google/, { timeout: 15000 });
    
    // 输入邮箱
    await page.fill('input[type="email"]', GOOGLE_EMAIL, { timeout: 10000 });
    await page.click('text=下一步', { timeout: 10000 });
    
    // 输入密码
    await page.waitForURL(/challenge|password/, { timeout: 10000 });
    await page.fill('input[type="password"]', GOOGLE_PASSWORD, { timeout: 10000 });
    await page.click('text=下一步', { timeout: 10000 });
    
    // 等待跳转回 ACLClouds
    await page.waitForURL(`${BASE_URL}/projects`, { timeout: 30000 });
    
    log('✅ Google 登录成功！');
}

// ==================== 获取 Cookie ====================
async function getCookieFromBrowser(page) {
    const cookies = await page.context().cookies([BASE_URL]);
    const cookieStr = cookies.map(c => `${c.name}=${c.value}`).join('; ');
    return cookieStr;
}

// ==================== 续期逻辑 ====================
async function renewWithCookie(cookieStr) {
    log('\n🔄 开始续期流程...');
    
    const session = axios.create({
        baseURL: BASE_URL,
        timeout: 30000,
        headers: {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
    });
    
    // 设置 Cookie
    session.defaults.headers.common['Cookie'] = cookieStr;
    
    // 获取 XSRF Token
    const xsrfMatch = cookieStr.match(/XSRF-TOKEN=([^;]+)/);
    const xsrfToken = xsrfMatch ? decodeURIComponent(xsrfMatch[1]) : null;
    
    if (xsrfToken) {
        session.defaults.headers.common['X-XSRF-TOKEN'] = xsrfToken;
    }
    
    // 获取服务器列表
    let servers = [];
    try {
        const resp = await session.get('/api/client');
        servers = resp.data?.data || [];
        log(`📦 共 ${servers.length} 台服务器`);
    } catch (e) {
        log(`❌ 获取服务器列表失败: ${e.response?.status} ${e.message}`);
        return;
    }
    
    const now = new Date();
    let renewed = 0, skipped = 0, failed = 0;
    const results = [];
    
    for (let i = 0; i < servers.length; i++) {
        const srv = servers[i];
        const attrs = srv.attributes || srv;
        const sid = attrs.identifier || attrs.id || attrs.uuid;
        const name = attrs.name || `server-${i + 1}`;
        
        if (!sid) {
            results.push(`⚠️ ${name}: 缺少 server id`);
            continue;
        }
        
        log(`\n[${i + 1}/${servers.length}] 🖥️ ${name} (id=${sid})`);
        
        // 获取到期时间
        let expireAt = null;
        for (const key of ['expires_at', 'expire_at', 'expiration_date']) {
            if (attrs[key]) {
                expireAt = new Date(attrs[key]);
                break;
            }
        }
        
        if (!expireAt) {
            // 尝试获取详情
            try {
                const detailResp = await session.get(`/api/client/servers/${sid}`);
                const detail = detailResp.data?.attributes || detailResp.data;
                for (const key of ['expires_at', 'expire_at', 'expiration_date']) {
                    if (detail[key]) {
                        expireAt = new Date(detail[key]);
                        break;
                    }
                }
            } catch (e) {
                log(`⚠️ ${name}: 无法获取详情`);
                skipped++;
                continue;
            }
        }
        
        if (!expireAt) {
            results.push(`⚠️ ${name}: 无到期时间字段`);
            skipped++;
            continue;
        }
        
        const remainingMs = expireAt - now;
        const remainingHrs = remainingMs / (1000 * 60 * 60);
        const remainingFmt = fmtRemaining(remainingMs / 1000);
        
        log(`📅 到期: ${expireAt.toISOString()}  剩余: ${remainingFmt}`);
        
        // 检查是否到达续期阈值
        if (remainingHrs > RENEW_THRESHOLD_HOURS) {
            results.push(`⏭️ ${name}: 剩 ${remainingFmt}, 未到阈值 ${RENEW_THRESHOLD_HOURS}h`);
            skipped++;
            continue;
        }
        
        // 执行续期
        try {
            const renewResp = await session.post(`/api/client/servers/${sid}/upgrade/renew`);
            
            if ([200, 201, 202, 204].includes(renewResp.status)) {
                // 重新获取到期时间
                await new Promise(r => setTimeout(r, 1500));
                const newDetailResp = await session.get(`/api/client/servers/${sid}`);
                const newDetail = newDetailResp.data?.attributes || newDetailResp.data;
                
                let newExpireAt = null;
                for (const key of ['expires_at', 'expire_at', 'expiration_date']) {
                    if (newDetail[key]) {
                        newExpireAt = new Date(newDetail[key]);
                        break;
                    }
                }
                
                const newRemaining = newExpireAt ? fmtRemaining((newExpireAt - now) / 1000) : '?';
                results.push(`✅ ${name}: ${remainingFmt} → ${newRemaining}`);
                renewed++;
                log(`✅ 续期成功: ${remainingFmt} → ${newRemaining}`);
            } else {
                results.push(`❌ ${name}: HTTP ${renewResp.status}`);
                failed++;
                log(`❌ 续期失败: HTTP ${renewResp.status}`);
            }
        } catch (e) {
            results.push(`❌ ${name}: ${e.message}`);
            failed++;
            log(`❌ 续期异常: ${e.message}`);
        }
        
        await new Promise(r => setTimeout(r, 2000)); // 礼貌延时
    }
    
    // 发送总结
    const summary = [
        '🎮 *ACLClouds 自动续期*',
        `⏰ ${now.toISOString()}`,
        '',
        `📊 总服务器: ${servers.length} | ✅ ${renewed} | ⏭️ ${skipped} | ❌ ${failed}`,
        '',
        ...results.map(r => `  ${r}`),
    ].join('\n');
    
    log('\n' + summary + '\n');
    await sendTelegram(summary);
}

// ==================== 主入口 ====================
async function main() {
    log('🚀 ACLClouds Google 自动续期脚本启动');
    log(`📧 Google 邮箱: ${GOOGLE_EMAIL}`);
    log(`⚙️ 续期阈值: ${RENEW_THRESHOLD_HOURS}h`);
    
    if (!GOOGLE_EMAIL || !GOOGLE_PASSWORD) {
        log('❌ 未配置 ACL_GOOGLE_EMAIL 或 ACL_GOOGLE_PASSWORD');
        process.exit(1);
    }
    
    let browser = null;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext();
        const page = await context.newPage();
        
        // Google 登录
        await loginWithGoogle(page);
        
        // 获取 Cookie
        const cookieStr = await getCookieFromBrowser(page);
        log(`🍪 Cookie 长度: ${cookieStr.length}`);
        
        // 续期
        await renewWithCookie(cookieStr);
        
    } catch (e) {
        log(`❌ 运行失败: ${e.message}`);
        await sendTelegram(`❌ ACLClouds 续期失败: ${e.message}`);
    } finally {
        if (browser) {
            await browser.close();
        }
    }
}

main();
