// ShapeKey blend compute shader (v0.2, TheHerta3 architecture)
//
// 设计要点（详见 SHAPEKEY_ANALYSIS_REPORT.md）：
//   1. VertexAttributes 用 struct{float3 position; uint normal;}，法线按
//      uint 存取 —— 避免 v0.1 系列里把 uint normal 当 float 读写导致的
//      NaN/denormal 规范化，从而保证写回后法线 bit 完全保留。
//   2. RWStructuredBuffer 在 register(u5)。上游 ini 用
//         cs-u5 = copy Resource_Component<cid>_Position_0
//         Resource_Component<cid>_VB0 = ref cs-u5
//      把本帧所有 vb0 引用重定向到这个 UAV。
//   3. freq_indices (t99) 是形态键 v.s. 权重通道的查表，255 表示
//      该顶点在该 slot 上无形变，分支直接跳过未绑定 slot。
//   4. MAX_SLOTS=24，受 DX11 SM5.0 SRV 寄存器上限（t127）约束：
//      shapekey_pos_deltas 占用 t51..t74，shapekey_maps 占用 t75..t98。
//      若需要更多槽位，请启用 Merge Buffers 模式（最多 128 槽位）。
//      slot 索引（0 起步）使用 —— baker 里 -1 做了偏移。

#define MAX_SLOTS       24
#define NO_FREQ_INDEX   255

struct VertexAttributes {
    float3 position;
    uint   normal;   // ★ 终末地 VB0 stride=16 的第 4 字节是打包法线，必须声明为 uint
};

RWStructuredBuffer<VertexAttributes> rw_buffer : register(u5);

StructuredBuffer<float3> shapekey_pos_deltas[MAX_SLOTS] : register(t51);
StructuredBuffer<int>    shapekey_maps[MAX_SLOTS]       : register(t75);
StructuredBuffer<uint>   vertex_freq_indices            : register(t99);

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
    for (int s = 0; s < MAX_SLOTS; ++s) {
        uint fi = vertex_freq_indices[i * MAX_SLOTS + s];
        if (fi != NO_FREQ_INDEX) {
            float w = IniParams.Load(int3(100 + (int)fi, 0, 0)).x;
            int pi = shapekey_maps[s][i];
            if (pi >= 0) {
                acc += shapekey_pos_deltas[s][pi] * w;
            }
        }
    }

    v.position += acc;      // 只改 position；.normal 按 uint 原样写回
    rw_buffer[i] = v;
}
