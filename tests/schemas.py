# tests/schemas.py — 接口响应 Pydantic 契约模型（严格对应 mock_server 真实响应结构）
from pydantic import BaseModel, Field


class OrderDataModel(BaseModel):
    order_id: int = Field(..., gt=0, description="数据库自增订单ID")
    item_name: str = Field(..., min_length=1, description="商品名称")
    current_status: str = Field(..., pattern="^(PENDING|PAID|CANCELLED)$", description="订单状态机状态")


class OrderResponseSchema(BaseModel):
    code: int = Field(..., description="业务状态码")
    message: str = Field(..., description="响应消息")
    data: OrderDataModel
