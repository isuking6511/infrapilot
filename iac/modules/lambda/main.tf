# Lambda 실행 IAM 역할
resource "aws_iam_role" "lambda" {
  name = "infrapilot-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# VPC 접근 + CloudWatch 로그 권한
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Lambda 함수
resource "aws_lambda_function" "collector" {
  function_name = "infrapilot-collector"
  package_type  = "Image"
  image_uri     = var.ecr_image_uri
  role          = aws_iam_role.lambda.arn
  timeout       = 300   # 5분 (160개 심볼 수집 시간)
  memory_size   = 512

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.lambda_sg_id]
  }

  environment {
    variables = {
      DB_HOST     = var.db_host
      DB_PORT     = "5432"
      DB_NAME     = "infrapilot"
      DB_USER     = "infrapilot"
      DB_PASSWORD = var.db_password
    }
  }

  tags = {
    Name = "infrapilot-collector"
  }
}

# EventBridge
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "infrapilot-collector-schedule"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.schedule.name
  arn  = aws_lambda_function.collector.arn
}

# EventBridge가 Lambda 호출할 수 있는 권한
resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}

# ── Analyzer Lambda (15분마다 상위 50개 엘리어트 파동 분석) ──────────────────

resource "aws_lambda_function" "analyzer" {
  function_name = "infrapilot-analyzer"
  package_type  = "Image"
  image_uri     = var.ecr_image_uri
  role          = aws_iam_role.lambda.arn
  timeout       = 900  # 50개 심볼 × API 호출 시간 여유
  memory_size   = 512

  image_config {
    command = ["infrapilot.lambda_.analyzer.handler"]
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.lambda_sg_id]
  }

  environment {
    variables = {
      DB_HOST        = var.db_host
      DB_PORT        = "5432"
      DB_NAME        = "infrapilot"
      DB_USER        = "infrapilot"
      DB_PASSWORD    = var.db_password
      GEMINI_API_KEY = var.gemini_api_key
    }
  }

  tags = {
    Name = "infrapilot-analyzer"
  }
}

resource "aws_cloudwatch_event_rule" "analyzer_schedule" {
  name                = "infrapilot-analyzer-schedule"
  schedule_expression = "rate(15 minutes)"
}

resource "aws_cloudwatch_event_target" "analyzer" {
  rule = aws_cloudwatch_event_rule.analyzer_schedule.name
  arn  = aws_lambda_function.analyzer.arn
}

resource "aws_lambda_permission" "analyzer_eventbridge" {
  statement_id  = "AllowEventBridgeAnalyzer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analyzer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.analyzer_schedule.arn
}