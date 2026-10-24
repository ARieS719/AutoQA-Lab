# tests/schemas.py
from typing import Optional
from pydantic import BaseModel, Field

class OrderDataModel(BaseModel):
    order_id: int = Field(..., gt=0, description="数据库自增订单ID")
    item_name: str = Field(..., min_length=1, description="商品名称")
    current_status: str = Field(..., pattern="^(PENDING|PAID|CANCELLED)$", description="订单状态机状态")
    timestamp: Optional[int] = Field(None, description="可选时间戳")

class OrderResponseSchema(BaseModel):
    code: int = Field(..., description="业务状态码")
    message: str = Field(..., description="响应消息")
    data: OrderDataModel