// ShapeKey blend compute shader (v0.2.2 merged variant)
//
// 与 shapekey_blend.hlsl 同架构（u5 RWStructuredBuffer, struct{float3, uint}），
// 区别：每个 component 只有 1 份 deltas + 1 份 lookup buffer，
// 不再需要 freq_indices 文件 —— channel_idx == slot 是确定的。
//
// ini 端绑定：
//   cs-t51 = copy Resource_Component<cid>_Position_deltas       ; StructuredBuffer<float3>
//   cs-t52 = copy Resource_Component<cid>_Position_lookup       ; StructuredBuffer<int>
// 解释：
//   shapekey_lookup[i * MAX_SLOTS + s] = -1 表示该 (vertex, slot) 无形变；
//   否则它就是合并 deltas 中的偏移（int32），shader 直接 deltas[off] 取 delta。
//   slot s 对应 IniParams[100 + s].x 的强度。

#define MAX_SLOTS 128

// ACTIVE_SHAPE_SLOTS_BEGIN
#define ACTIVE_SHAPE_SLOT_COUNT 0
static const int ACTIVE_SHAPE_SLOTS[1] = {
    0
};
// ACTIVE_SHAPE_SLOTS_END

struct VertexAttributes {
    float3 position;
    uint   normal;
};

RWStructuredBuffer<VertexAttributes> rw_buffer        : register(u5);
StructuredBuffer<float3>             shapekey_deltas  : register(t51);
StructuredBuffer<int>                shapekey_lookup  : register(t52);

Texture1D<float4> IniParams : register(t120);

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint i = tid.x;

    uint vcount, stride;
    rw_buffer.GetDimensions(vcount, stride);
    if (i >= vcount) return;

    VertexAttributes v = rw_buffer[i];
    float3 acc = float3(0.0, 0.0, 0.0);

    [unroll]
    for (int k = 0; k < ACTIVE_SHAPE_SLOT_COUNT; ++k) {
        int s = ACTIVE_SHAPE_SLOTS[k];
        int off = shapekey_lookup[i * MAX_SLOTS + s];
        if (off >= 0) {
            float w = IniParams.Load(int3(100 + s, 0, 0)).x;
            acc += shapekey_deltas[off] * w;
        }
    }

    v.position += acc;
    rw_buffer[i] = v;
}
