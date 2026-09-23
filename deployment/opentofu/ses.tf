locals {
  ses_domain    = coalesce(var.ses_domain, split("@", var.smtp_from)[1])
  ses_mail_from = "${var.ses_mail_from_subdomain}.${local.ses_domain}"

  smtp_host     = var.ses_enabled ? "email-smtp.${var.aws_region}.amazonaws.com" : var.smtp_host
  smtp_user     = var.ses_enabled ? aws_iam_access_key.ses_smtp[0].id : var.smtp_user
  smtp_password = var.ses_enabled ? aws_iam_access_key.ses_smtp[0].ses_smtp_password_v4 : var.smtp_password
}

resource "aws_sesv2_email_identity" "pretalx" {
  count          = var.ses_enabled ? 1 : 0
  email_identity = local.ses_domain
}

resource "aws_route53_record" "ses_dkim" {
  count   = var.ses_enabled ? 3 : 0
  zone_id = var.route53_zone_id
  name    = "${aws_sesv2_email_identity.pretalx[0].dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.ses_domain}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_sesv2_email_identity.pretalx[0].dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_sesv2_email_identity_mail_from_attributes" "pretalx" {
  count                  = var.ses_enabled ? 1 : 0
  email_identity         = aws_sesv2_email_identity.pretalx[0].email_identity
  mail_from_domain       = local.ses_mail_from
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_route53_record" "ses_mail_from_mx" {
  count   = var.ses_enabled ? 1 : 0
  zone_id = var.route53_zone_id
  name    = local.ses_mail_from
  type    = "MX"
  ttl     = 600
  records = ["10 feedback-smtp.${var.aws_region}.amazonses.com"]
}

resource "aws_route53_record" "ses_mail_from_spf" {
  count   = var.ses_enabled ? 1 : 0
  zone_id = var.route53_zone_id
  name    = local.ses_mail_from
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com -all"]
}

resource "aws_route53_record" "ses_dmarc" {
  count   = var.ses_enabled && var.ses_dmarc_policy != null ? 1 : 0
  zone_id = var.route53_zone_id
  name    = "_dmarc.${local.ses_domain}"
  type    = "TXT"
  ttl     = 600
  records = ["v=DMARC1; p=${var.ses_dmarc_policy}"]
}

resource "aws_iam_user" "ses_smtp" {
  count = var.ses_enabled ? 1 : 0
  name  = "${local.identifier}-ses-smtp"
}

data "aws_iam_policy_document" "ses_smtp" {
  count = var.ses_enabled ? 1 : 0

  statement {
    actions = ["ses:SendRawEmail"]
    # In the SES sandbox, SES also authorizes against the recipient's verified
    # identity, so allow all identities; the FromAddress condition still limits
    # which addresses may send.
    resources = ["arn:aws:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/*"]

    condition {
      test     = "StringLike"
      variable = "ses:FromAddress"
      values   = ["*@${local.ses_domain}"]
    }
  }
}

resource "aws_iam_user_policy" "ses_smtp" {
  count  = var.ses_enabled ? 1 : 0
  name   = "ses-send"
  user   = aws_iam_user.ses_smtp[0].name
  policy = data.aws_iam_policy_document.ses_smtp[0].json
}

resource "aws_iam_access_key" "ses_smtp" {
  count = var.ses_enabled ? 1 : 0
  user  = aws_iam_user.ses_smtp[0].name
}
