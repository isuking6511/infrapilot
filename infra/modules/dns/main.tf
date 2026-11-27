resource "aws_route53_zone" "this" {
  name = var.domain_name

  lifecycle {
    prevent_destroy = true  
  }
}

resource "aws_route53_record" "apex" {
  zone_id = aws_route53_zone.this.zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 300
  records = [var.target_ip]
}

