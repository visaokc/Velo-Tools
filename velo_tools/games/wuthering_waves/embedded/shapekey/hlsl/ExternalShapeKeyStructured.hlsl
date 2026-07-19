Texture1D<float4> IniParams : register(t120);

#define VertexCount uint(IniParams[0].x)

Buffer<uint> VertexOffsets : register(t50);
Buffer<uint> RecordChannels : register(t51);
Buffer<float3> RecordDeltas : register(t52);
Buffer<float3> BasePosition : register(t6);
RWBuffer<float> OutputPosition : register(u6);

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint vertex_id = tid.x;
    if (vertex_id >= VertexCount) {
        return;
    }

    float3 position = BasePosition.Load(vertex_id);
    uint first = VertexOffsets[vertex_id];
    uint end = VertexOffsets[vertex_id + 1];
    [loop]
    for (uint record_id = first; record_id < end; ++record_id) {
        uint channel = RecordChannels[record_id];
        float weight = IniParams[100 + channel].x;
        position += RecordDeltas[record_id] * weight;
    }

    uint output_id = vertex_id * 3;
    OutputPosition[output_id + 0] = position.x;
    OutputPosition[output_id + 1] = position.y;
    OutputPosition[output_id + 2] = position.z;
}
