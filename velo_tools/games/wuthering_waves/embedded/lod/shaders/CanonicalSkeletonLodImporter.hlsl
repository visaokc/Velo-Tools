// Canonical skeleton LOD importer.
// Scatters the current native LOD palette into stable canonical bone slots.

cbuffer NativeSkeleton : register(b8)
{
    float4 NativeSkeletonRows[768];
}

Buffer<uint> BoneMap : register(t36);
RWBuffer<float4> CanonicalSkeleton : register(u6);
Texture1D<float4> IniParams : register(t120);

#define PairCount ((uint)IniParams[0].x)
#define DestinationOffset ((uint)IniParams[0].y)
#define CustomMeshScale IniParams[0].z

[numthreads(64, 1, 1)]
void main(uint3 thread_id : SV_DispatchThreadID)
{
    uint pair_id = thread_id.x;
    if (pair_id >= PairCount)
        return;

    uint source = BoneMap[pair_id] * 3;
    uint destination = (DestinationOffset + pair_id) * 3;

    CanonicalSkeleton[destination + 0] =
        NativeSkeletonRows[source + 0] * CustomMeshScale;
    CanonicalSkeleton[destination + 1] =
        NativeSkeletonRows[source + 1] * CustomMeshScale;
    CanonicalSkeleton[destination + 2] =
        NativeSkeletonRows[source + 2] * CustomMeshScale;
}
