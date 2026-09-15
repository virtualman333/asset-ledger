from django.conf import settings
from django.db import models


class Evidence(models.Model):
    """原始凭证（截图/文本）。永不硬删，用于溯源与纠错训练。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="evidences", verbose_name="用户")
    file = models.ImageField("截图", upload_to="evidence/%Y/%m/", null=True, blank=True)
    ocr_text = models.TextField("识别原文", blank=True)
    sha256 = models.CharField("文件指纹", max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "原始凭证"
        verbose_name_plural = "原始凭证"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Evidence#{self.id} {self.created_at:%Y-%m-%d %H:%M}"


class IngestJob(models.Model):
    """Agent 抽取任务。AI 只产出草稿，确认后才写入流水。"""

    class Kind(models.TextChoices):
        IMAGE = "IMAGE", "截图"
        TEXT = "TEXT", "文本"

    class Status(models.TextChoices):
        PENDING = "PENDING", "处理中"
        SUCCEEDED = "SUCCEEDED", "成功"
        FAILED = "FAILED", "失败"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ingest_jobs", verbose_name="用户")
    kind = models.CharField("类型", max_length=16, choices=Kind.choices, default=Kind.IMAGE)
    evidence = models.ForeignKey(Evidence, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs", verbose_name="凭证")
    raw_text = models.TextField("原始文本", blank=True)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.PENDING)
    result_json = models.JSONField("抽取结果", default=dict, blank=True)
    confidence = models.FloatField("置信度", default=0)
    missing = models.JSONField("缺失字段", default=list, blank=True)
    error = models.TextField("错误信息", blank=True)
    client_request_id = models.CharField("幂等键", max_length=64, null=True, blank=True, unique=True, db_index=True)
    confirmed_at = models.DateTimeField("确认入账时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "抽取任务"
        verbose_name_plural = "抽取任务"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"IngestJob#{self.id} {self.status}"
