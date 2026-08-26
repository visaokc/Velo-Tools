// Initializes uncaptured canonical bones to identity transforms.

RWBuffer<float4> CanonicalSkeleton : register(u6);
Texture1D<float4> IniParams : register(t120);

#define BoneCount ((uint)IniParams[0].x)

[numthreads(64, 1, 1)]
void main(uint3 thread_id : SV_DispatchThreadID)
{
    uint bone_id = thread_id.x;
    if (bone_id >= BoneCount)
        return;

    uint destination = bone_id * 3;
    CanonicalSkeleton[destination + 0] = float4(1, 0, 0, 0);
    CanonicalSkeleton[destination + 1] = float4(0, 1, 0, 0);
    CanonicalSkeleton[destination + 2] = float4(0, 0, 1, 0);
}
