"""Classify observed official login checkpoints without treating them as login."""


def douyin_verification(body):
    if any(text in body for text in ("身份验证", "验证身份", "安全验证", "请完成验证", "验证登录密码")):
        return {"kind": "identity", "message": "抖音要求额外身份验证，请在下方官方验证界面完成"}
    if any(text in body for text in ("扫码成功", "需在手机上进行确认", "请在手机上确认", "请在抖音上确认")):
        return {"kind": "phone", "message": "扫码已收到，请在抖音 App 确认登录"}
    return None
