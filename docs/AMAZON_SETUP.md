# Amazon 接入与开通指南

适用范围：先为自己的卖家账号建立私有 SP-API 应用。当前项目已经实现订单履约摘要和可用消息类型查询，尚未接通客户来信、客户身份与订单绑定、工单自动取真实订单或实际发送。

## 1. 准备卖家账号

需要自己组织的 Amazon 卖家账号及有权限的主账号负责人。通过 Seller Central 开发私有卖家应用需要专业销售账号；没有卖家账号时，先完成卖家注册，再进行 SP-API 开通。参考 [官方注册总览](https://developer-docs.amazon/sp-api/docs/sp-api-registration-overview)。

如果最终为其他公司的商家提供服务，需要改走公开应用授权流程，不能使用“自有店铺私有应用”代替第三方商家授权。

## 2. 注册私有开发者

进入 [Solution Provider Portal](https://solutionproviderportal.amazon.com/)，按自己的业务情况注册开发者资料，说明仅管理自己组织的卖家业务。填写真实联系人、数据用途及安全措施；完成门户要求的审核。入口和审核状态因账号不同而异，按门户实际显示操作。参考 [私有开发者开通](https://developer.amazonservices.com/start/getting-started-for-private-developers)。

## 3. 创建 SP-API 应用

门户中选择 Develop Apps → Add new app client，创建针对 Sellers 的应用。只接自己的店铺时选私有应用。根据 [订单读取文档](https://developer-docs.amazon/sp-api/lang-us/docs/get-order-information)申请所需订单读取角色；查询买家可用消息类型需要 Buyer Communication 角色。当前适配器不请求 BUYER 或 RECIPIENT 数据集，不需要先为本轮功能申请姓名、地址访问权限。参考 [应用注册步骤](https://developer-docs.amazon/sp-api/docs/registering-your-application)。

## 4. 卖家自授权

找到对应应用，使用卖家账号的主用户执行 Authorize app，取得该账号的 refresh token。私有应用可以保持 draft 状态进行自授权，无须为了内部使用而发布到应用商店。然后在应用凭据页取得 LWA client ID、client secret。参考 [自授权步骤](https://developer-docs.amazon/sp-api/docs/self-authorization)。

这些凭据仅配置到本地 `.env` 或服务端密钥管理器，不填进网页、聊天或 GitHub。控制台中的“访问令牌”是本项目 DEMO_TOKEN，与 Amazon refresh token 不同。

## 5. 配置本项目

在项目根目录复制 `.env.example` 为 `.env`（已有 `.env` 时只补充字段）。填写：

```dotenv
AMAZON_ENABLED=true
AMAZON_SANDBOX=true
AMAZON_REGION=na
AMAZON_MARKETPLACE_ID=填写实际站点ID
AMAZON_CLIENT_ID=填写应用LWA客户端ID
AMAZON_CLIENT_SECRET=填写应用LWA密钥
AMAZON_REFRESH_TOKEN=填写卖家自授权refresh_token
```

region 可为 na / eu / fe；marketplace ID 是店铺所在站点的标识，不是 seller ID、订单号或站点名称。先使用沙箱，使用订单 API 的官方沙箱测试订单验证响应；沙箱数据不代表真实店铺数据。参考 [沙箱说明](https://developer-docs.amazon/sp-api/docs/sp-api-sandbox)。

启动主服务和静态前端：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# 另开终端
.venv/bin/python -m http.server 8137 --bind 127.0.0.1 --directory web
```

浏览器打开 `http://127.0.0.1:8137`，在“连接后台服务”中填写 `http://127.0.0.1:8000` 和本项目 DEMO_TOKEN。打开“Amazon 订单核查”，输入 3-7-7 位数字格式的沙箱订单号。

服务端通过 LWA 换取访问令牌并在内存复用；订单接口仅请求 FULFILLMENT，输出字段白名单过滤姓名、邮箱、地址等数据。

## 6. 核对结果

- `/integrations/amazon/status`：检查 enabled/configured 和缺失配置项，接口要求项目鉴权。
- `/integrations/amazon/orders/{订单号}`：核对站点与履约状态。
- `/integrations/amazon/orders/{订单号}/message-actions`：核对允许的消息类型；不代表支持任意客服回复。
- 503：未启用或配置不完整；403/404：角色、卖家授权、订单或站点需核对；429：接口限流；502：授权交换或 Amazon 服务异常。

沙箱通过后，再把 `AMAZON_SANDBOX=false`，用该账号的实际订单进行只读核查。下一阶段需要明确买家身份来源和接收客户来信的渠道，再实现订单归属校验及实际发送。

Amazon 的 [Messaging API](https://developer-docs.amazon/sp-api/docs/messaging-api)按订单返回当前允许的消息类型，发送前要按相应类型的 schema 和条件处理。当前没有把 AI 草稿接成通用自动发送接口。

## 验证边界

自动测试使用 httpx MockTransport，覆盖令牌缓存、固定域名、错误信息隐藏、订单格式、站点不匹配、只返回允许字段及不跟随外部消息链接。未配置真实卖家凭据，未进行 Amazon 实际 API 联调。
