#define NOMINMAX

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <d3d11.h>
#include <d3dcompiler.h>
#include <wrl/client.h>

using Microsoft::WRL::ComPtr;

namespace
{
	constexpr uint32_t kResolution = 16;
	constexpr uint32_t kSampleCount = kResolution * kResolution;
	constexpr uint32_t kDdsFourCc = 0x4;
	constexpr uint32_t kDdsRgb = 0x40;
	constexpr uint32_t kDdsLuminance = 0x20000;

	#pragma pack(push, 1)
	struct DdsPixelFormat
	{
		uint32_t size;
		uint32_t flags;
		uint32_t four_cc;
		uint32_t rgb_bits;
		uint32_t r_mask;
		uint32_t g_mask;
		uint32_t b_mask;
		uint32_t a_mask;
	};

	struct DdsHeader
	{
		uint32_t size;
		uint32_t flags;
		uint32_t height;
		uint32_t width;
		uint32_t pitch_or_linear_size;
		uint32_t depth;
		uint32_t mip_count;
		uint32_t reserved1[11];
		DdsPixelFormat pixel_format;
		uint32_t caps;
		uint32_t caps2;
		uint32_t caps3;
		uint32_t caps4;
		uint32_t reserved2;
	};

	struct DdsHeaderDx10
	{
		uint32_t dxgi_format;
		uint32_t resource_dimension;
		uint32_t misc_flag;
		uint32_t array_size;
		uint32_t misc_flags2;
	};

	struct FingerprintParams
	{
		uint32_t source_mip;
		uint32_t padding[3];
	};

	struct Sample
	{
		float channels[4];
	};
	#pragma pack(pop)

	constexpr uint32_t FourCc(char a, char b, char c, char d)
	{
		return
			static_cast<uint32_t>(a) |
			static_cast<uint32_t>(b) << 8 |
			static_cast<uint32_t>(c) << 16 |
			static_cast<uint32_t>(d) << 24;
	}

	struct DdsImage
	{
		uint32_t width = 0;
		uint32_t height = 0;
		DXGI_FORMAT resource_format = DXGI_FORMAT_UNKNOWN;
		DXGI_FORMAT view_format = DXGI_FORMAT_UNKNOWN;
		uint32_t row_pitch = 0;
		uint32_t slice_pitch = 0;
		std::vector<uint8_t> pixels;
	};

	std::string Narrow(const std::wstring& value)
	{
		if (value.empty())
			return {};
		const int size = WideCharToMultiByte(
			CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
			nullptr, 0, nullptr, nullptr);
		std::string result(size, '\0');
		WideCharToMultiByte(
			CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
			result.data(), size, nullptr, nullptr);
		return result;
	}

	bool ReadFile(const wchar_t* path, std::vector<uint8_t>* bytes, std::string* error)
	{
		std::ifstream stream(path, std::ios::binary | std::ios::ate);
		if (!stream) {
			*error = "Cannot open DDS file";
			return false;
		}
		const std::streamoff size = stream.tellg();
		if (size < 0) {
			*error = "Cannot determine DDS file size";
			return false;
		}
		bytes->resize(static_cast<size_t>(size));
		stream.seekg(0);
		if (!stream.read(
			reinterpret_cast<char*>(bytes->data()),
			static_cast<std::streamsize>(bytes->size()))) {
			*error = "Cannot read DDS file";
			return false;
		}
		return true;
	}

	bool ResolveLegacyFormat(
		const DdsPixelFormat& pixel_format,
		DXGI_FORMAT* resource_format,
		DXGI_FORMAT* view_format)
	{
		if (pixel_format.flags & kDdsFourCc) {
			switch (pixel_format.four_cc) {
			case FourCc('D', 'X', 'T', '1'):
				*resource_format = *view_format = DXGI_FORMAT_BC1_UNORM;
				return true;
			case FourCc('D', 'X', 'T', '2'):
			case FourCc('D', 'X', 'T', '3'):
				*resource_format = *view_format = DXGI_FORMAT_BC2_UNORM;
				return true;
			case FourCc('D', 'X', 'T', '4'):
			case FourCc('D', 'X', 'T', '5'):
				*resource_format = *view_format = DXGI_FORMAT_BC3_UNORM;
				return true;
			case FourCc('A', 'T', 'I', '1'):
			case FourCc('B', 'C', '4', 'U'):
				*resource_format = *view_format = DXGI_FORMAT_BC4_UNORM;
				return true;
			case FourCc('A', 'T', 'I', '2'):
			case FourCc('B', 'C', '5', 'U'):
				*resource_format = *view_format = DXGI_FORMAT_BC5_UNORM;
				return true;
			default:
				return false;
			}
		}

		if ((pixel_format.flags & kDdsLuminance) &&
			pixel_format.rgb_bits == 8 &&
			pixel_format.r_mask == 0xff) {
			*resource_format = *view_format = DXGI_FORMAT_R8_UNORM;
			return true;
		}

		if (!(pixel_format.flags & kDdsRgb) || pixel_format.rgb_bits != 32)
			return false;
		if (pixel_format.r_mask == 0x00ff0000 &&
			pixel_format.g_mask == 0x0000ff00 &&
			pixel_format.b_mask == 0x000000ff) {
			*resource_format = *view_format =
				pixel_format.a_mask
					? DXGI_FORMAT_B8G8R8A8_UNORM
					: DXGI_FORMAT_B8G8R8X8_UNORM;
			return true;
		}
		if (pixel_format.r_mask == 0x000000ff &&
			pixel_format.g_mask == 0x0000ff00 &&
			pixel_format.b_mask == 0x00ff0000) {
			*resource_format = *view_format = DXGI_FORMAT_R8G8B8A8_UNORM;
			return true;
		}
		return false;
	}

	bool ResolveDx10Format(
		DXGI_FORMAT source_format,
		DXGI_FORMAT* resource_format,
		DXGI_FORMAT* view_format)
	{
		switch (source_format) {
		case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_R8G8B8A8_TYPELESS;
			*view_format = DXGI_FORMAT_R8G8B8A8_UNORM;
			return true;
		case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_B8G8R8A8_TYPELESS;
			*view_format = DXGI_FORMAT_B8G8R8A8_UNORM;
			return true;
		case DXGI_FORMAT_B8G8R8X8_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_B8G8R8X8_TYPELESS;
			*view_format = DXGI_FORMAT_B8G8R8X8_UNORM;
			return true;
		case DXGI_FORMAT_BC1_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_BC1_TYPELESS;
			*view_format = DXGI_FORMAT_BC1_UNORM;
			return true;
		case DXGI_FORMAT_BC2_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_BC2_TYPELESS;
			*view_format = DXGI_FORMAT_BC2_UNORM;
			return true;
		case DXGI_FORMAT_BC3_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_BC3_TYPELESS;
			*view_format = DXGI_FORMAT_BC3_UNORM;
			return true;
		case DXGI_FORMAT_BC7_UNORM_SRGB:
			*resource_format = DXGI_FORMAT_BC7_TYPELESS;
			*view_format = DXGI_FORMAT_BC7_UNORM;
			return true;
		case DXGI_FORMAT_R8_UNORM:
		case DXGI_FORMAT_R8G8B8A8_UNORM:
		case DXGI_FORMAT_B8G8R8A8_UNORM:
		case DXGI_FORMAT_B8G8R8X8_UNORM:
		case DXGI_FORMAT_BC1_UNORM:
		case DXGI_FORMAT_BC2_UNORM:
		case DXGI_FORMAT_BC3_UNORM:
		case DXGI_FORMAT_BC4_UNORM:
		case DXGI_FORMAT_BC5_UNORM:
		case DXGI_FORMAT_BC7_UNORM:
			*resource_format = *view_format = source_format;
			return true;
		default:
			return false;
		}
	}

	bool SurfaceInfo(
		uint32_t width,
		uint32_t height,
		DXGI_FORMAT format,
		uint32_t* row_pitch,
		uint32_t* slice_pitch)
	{
		uint32_t block_bytes = 0;
		switch (format) {
		case DXGI_FORMAT_BC1_TYPELESS:
		case DXGI_FORMAT_BC1_UNORM:
		case DXGI_FORMAT_BC4_UNORM:
			block_bytes = 8;
			break;
		case DXGI_FORMAT_BC2_TYPELESS:
		case DXGI_FORMAT_BC2_UNORM:
		case DXGI_FORMAT_BC3_TYPELESS:
		case DXGI_FORMAT_BC3_UNORM:
		case DXGI_FORMAT_BC5_UNORM:
		case DXGI_FORMAT_BC7_TYPELESS:
		case DXGI_FORMAT_BC7_UNORM:
			block_bytes = 16;
			break;
		default:
			break;
		}
		if (block_bytes) {
			const uint32_t blocks_wide = std::max(1u, (width + 3) / 4);
			const uint32_t blocks_high = std::max(1u, (height + 3) / 4);
			*row_pitch = blocks_wide * block_bytes;
			*slice_pitch = *row_pitch * blocks_high;
			return true;
		}

		uint32_t bytes_per_pixel = 0;
		switch (format) {
		case DXGI_FORMAT_R8_UNORM:
			bytes_per_pixel = 1;
			break;
		case DXGI_FORMAT_R8G8B8A8_TYPELESS:
		case DXGI_FORMAT_R8G8B8A8_UNORM:
		case DXGI_FORMAT_B8G8R8A8_TYPELESS:
		case DXGI_FORMAT_B8G8R8A8_UNORM:
		case DXGI_FORMAT_B8G8R8X8_TYPELESS:
		case DXGI_FORMAT_B8G8R8X8_UNORM:
			bytes_per_pixel = 4;
			break;
		default:
			return false;
		}
		*row_pitch = width * bytes_per_pixel;
		*slice_pitch = *row_pitch * height;
		return true;
	}

	bool LoadDds(const wchar_t* path, DdsImage* image, std::string* error)
	{
		std::vector<uint8_t> bytes;
		if (!ReadFile(path, &bytes, error))
			return false;
		if (bytes.size() < 4 + sizeof(DdsHeader) ||
			std::memcmp(bytes.data(), "DDS ", 4) != 0) {
			*error = "Invalid DDS header";
			return false;
		}

		const auto* header =
			reinterpret_cast<const DdsHeader*>(bytes.data() + 4);
		if (header->size != sizeof(DdsHeader) ||
			header->pixel_format.size != sizeof(DdsPixelFormat) ||
			header->width == 0 || header->height == 0) {
			*error = "Unsupported DDS header";
			return false;
		}

		size_t data_offset = 4 + sizeof(DdsHeader);
		DXGI_FORMAT resource_format = DXGI_FORMAT_UNKNOWN;
		DXGI_FORMAT view_format = DXGI_FORMAT_UNKNOWN;
		if (header->pixel_format.four_cc == FourCc('D', 'X', '1', '0')) {
			if (bytes.size() < data_offset + sizeof(DdsHeaderDx10)) {
				*error = "Truncated DDS DX10 header";
				return false;
			}
			const auto* dx10 = reinterpret_cast<const DdsHeaderDx10*>(
				bytes.data() + data_offset);
			data_offset += sizeof(DdsHeaderDx10);
			if (dx10->resource_dimension != D3D11_RESOURCE_DIMENSION_TEXTURE2D ||
				dx10->array_size != 1 ||
				!ResolveDx10Format(
					static_cast<DXGI_FORMAT>(dx10->dxgi_format),
					&resource_format,
					&view_format)) {
				*error = "Unsupported DDS DX10 format";
				return false;
			}
		} else if (!ResolveLegacyFormat(
			header->pixel_format,
			&resource_format,
			&view_format)) {
			*error = "Unsupported legacy DDS format";
			return false;
		}

		uint32_t row_pitch = 0;
		uint32_t slice_pitch = 0;
		if (!SurfaceInfo(
			header->width,
			header->height,
			resource_format,
			&row_pitch,
			&slice_pitch) ||
			bytes.size() < data_offset + slice_pitch) {
			*error = "Invalid DDS surface data";
			return false;
		}

		image->width = header->width;
		image->height = header->height;
		image->resource_format = resource_format;
		image->view_format = view_format;
		image->row_pitch = row_pitch;
		image->slice_pitch = slice_pitch;
		image->pixels.assign(
			bytes.begin() + data_offset,
			bytes.begin() + data_offset + slice_pitch);
		return true;
	}

	bool CreateDevice(
		ComPtr<ID3D11Device>* device,
		ComPtr<ID3D11DeviceContext>* context,
		std::string* error)
	{
		const D3D_FEATURE_LEVEL levels[] = {
			D3D_FEATURE_LEVEL_11_1,
			D3D_FEATURE_LEVEL_11_0,
		};
		D3D_FEATURE_LEVEL level = D3D_FEATURE_LEVEL_11_0;
		HRESULT result = D3D11CreateDevice(
			nullptr,
			D3D_DRIVER_TYPE_HARDWARE,
			nullptr,
			0,
			levels,
			static_cast<UINT>(std::size(levels)),
			D3D11_SDK_VERSION,
			device->GetAddressOf(),
			&level,
			context->GetAddressOf());
		if (FAILED(result)) {
			result = D3D11CreateDevice(
				nullptr,
				D3D_DRIVER_TYPE_WARP,
				nullptr,
				0,
				levels,
				static_cast<UINT>(std::size(levels)),
				D3D11_SDK_VERSION,
				device->GetAddressOf(),
				&level,
				context->GetAddressOf());
		}
		if (FAILED(result)) {
			*error = "Cannot create D3D11 device";
			return false;
		}
		return true;
	}

	bool SampleTexture(
		const DdsImage& image,
		std::array<Sample, kSampleCount>* samples,
		std::string* error)
	{
		ComPtr<ID3D11Device> device;
		ComPtr<ID3D11DeviceContext> context;
		if (!CreateDevice(&device, &context, error))
			return false;

		D3D11_TEXTURE2D_DESC texture_desc = {};
		texture_desc.Width = image.width;
		texture_desc.Height = image.height;
		texture_desc.MipLevels = 1;
		texture_desc.ArraySize = 1;
		texture_desc.Format = image.resource_format;
		texture_desc.SampleDesc.Count = 1;
		texture_desc.Usage = D3D11_USAGE_IMMUTABLE;
		texture_desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
		D3D11_SUBRESOURCE_DATA texture_data = {};
		texture_data.pSysMem = image.pixels.data();
		texture_data.SysMemPitch = image.row_pitch;
		texture_data.SysMemSlicePitch = image.slice_pitch;
		ComPtr<ID3D11Texture2D> texture;
		if (FAILED(device->CreateTexture2D(
			&texture_desc,
			&texture_data,
			&texture))) {
			*error = "Cannot create D3D11 texture";
			return false;
		}

		D3D11_SHADER_RESOURCE_VIEW_DESC srv_desc = {};
		srv_desc.Format = image.view_format;
		srv_desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
		srv_desc.Texture2D.MipLevels = 1;
		ComPtr<ID3D11ShaderResourceView> srv;
		if (FAILED(device->CreateShaderResourceView(
			texture.Get(),
			&srv_desc,
			&srv))) {
			*error = "Cannot create canonical D3D11 SRV";
			return false;
		}

		const char shader_source[] =
			"Texture2D<float4> source_texture : register(t0);\n"
			"RWStructuredBuffer<float4> output_samples : register(u0);\n"
			"#define FP_RESOLUTION 16\n"
			"cbuffer FingerprintParams : register(b0) {"
			" uint source_mip; uint3 padding; };\n"
			"[numthreads(8, 8, 1)]\n"
			"void main(uint3 id : SV_DispatchThreadID) {\n"
			" if (id.x >= FP_RESOLUTION || id.y >= FP_RESOLUTION) return;\n"
			" uint width, height, levels;\n"
			" source_texture.GetDimensions(source_mip, width, height, levels);\n"
			" uint2 begin = id.xy * uint2(width, height) / FP_RESOLUTION;\n"
			" uint2 end = (id.xy + 1) * uint2(width, height) / FP_RESOLUTION;\n"
			" end = min(uint2(width, height), max(end, begin + 1));\n"
			" float4 sum = 0;\n"
			" for (uint y = begin.y; y < end.y; ++y) {\n"
			"  for (uint x = begin.x; x < end.x; ++x) {\n"
			"   sum += source_texture.Load(int3(x, y, source_mip));\n"
			"  }\n"
			" }\n"
			" uint sample_count = (end.x - begin.x) * (end.y - begin.y);\n"
			" output_samples[id.y * FP_RESOLUTION + id.x] = sum / sample_count;\n"
			"}\n";
		ComPtr<ID3DBlob> shader_blob;
		ComPtr<ID3DBlob> shader_errors;
		if (FAILED(D3DCompile(
			shader_source,
			sizeof(shader_source) - 1,
			nullptr,
			nullptr,
			nullptr,
			"main",
			"cs_5_0",
			D3DCOMPILE_OPTIMIZATION_LEVEL3,
			0,
			&shader_blob,
			&shader_errors))) {
			*error = shader_errors
				? std::string(
					static_cast<const char*>(shader_errors->GetBufferPointer()),
					shader_errors->GetBufferSize())
				: "Cannot compile fingerprint shader";
			return false;
		}
		ComPtr<ID3D11ComputeShader> shader;
		if (FAILED(device->CreateComputeShader(
			shader_blob->GetBufferPointer(),
			shader_blob->GetBufferSize(),
			nullptr,
			&shader))) {
			*error = "Cannot create fingerprint shader";
			return false;
		}

		D3D11_BUFFER_DESC output_desc = {};
		output_desc.ByteWidth = sizeof(Sample) * kSampleCount;
		output_desc.Usage = D3D11_USAGE_DEFAULT;
		output_desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
		output_desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
		output_desc.StructureByteStride = sizeof(Sample);
		ComPtr<ID3D11Buffer> output;
		if (FAILED(device->CreateBuffer(&output_desc, nullptr, &output))) {
			*error = "Cannot create fingerprint output buffer";
			return false;
		}
		D3D11_UNORDERED_ACCESS_VIEW_DESC uav_desc = {};
		uav_desc.Format = DXGI_FORMAT_UNKNOWN;
		uav_desc.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
		uav_desc.Buffer.NumElements = kSampleCount;
		ComPtr<ID3D11UnorderedAccessView> uav;
		if (FAILED(device->CreateUnorderedAccessView(
			output.Get(),
			&uav_desc,
			&uav))) {
			*error = "Cannot create fingerprint UAV";
			return false;
		}

		FingerprintParams params = {};
		D3D11_BUFFER_DESC params_desc = {};
		params_desc.ByteWidth = sizeof(params);
		params_desc.Usage = D3D11_USAGE_IMMUTABLE;
		params_desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
		D3D11_SUBRESOURCE_DATA params_data = {};
		params_data.pSysMem = &params;
		ComPtr<ID3D11Buffer> params_buffer;
		if (FAILED(device->CreateBuffer(
			&params_desc,
			&params_data,
			&params_buffer))) {
			*error = "Cannot create fingerprint parameter buffer";
			return false;
		}

		D3D11_BUFFER_DESC staging_desc = output_desc;
		staging_desc.Usage = D3D11_USAGE_STAGING;
		staging_desc.BindFlags = 0;
		staging_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
		staging_desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
		ComPtr<ID3D11Buffer> staging;
		if (FAILED(device->CreateBuffer(&staging_desc, nullptr, &staging))) {
			*error = "Cannot create fingerprint staging buffer";
			return false;
		}

		ID3D11ShaderResourceView* srvs[] = {srv.Get()};
		ID3D11UnorderedAccessView* uavs[] = {uav.Get()};
		ID3D11Buffer* constant_buffers[] = {params_buffer.Get()};
		context->CSSetShader(shader.Get(), nullptr, 0);
		context->CSSetShaderResources(0, 1, srvs);
		context->CSSetUnorderedAccessViews(0, 1, uavs, nullptr);
		context->CSSetConstantBuffers(0, 1, constant_buffers);
		context->Dispatch(2, 2, 1);
		context->CopyResource(staging.Get(), output.Get());

		D3D11_MAPPED_SUBRESOURCE mapped = {};
		if (FAILED(context->Map(
			staging.Get(),
			0,
			D3D11_MAP_READ,
			0,
			&mapped))) {
			*error = "Cannot read fingerprint samples";
			return false;
		}
		std::memcpy(samples->data(), mapped.pData, sizeof(Sample) * kSampleCount);
		context->Unmap(staging.Get(), 0);
		return true;
	}

	float ClampUnit(float value)
	{
		return std::max(0.0f, std::min(1.0f, value));
	}

	uint8_t QuantizeUnit(float value)
	{
		return static_cast<uint8_t>(std::lround(ClampUnit(value) * 255.0f));
	}

	std::string EncodeBase64Url(const uint8_t* data, size_t size)
	{
		static const char alphabet[] =
			"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
		std::string encoded;
		encoded.reserve((size + 2) / 3 * 4);
		for (size_t i = 0; i < size; i += 3) {
			const size_t remaining = size - i;
			const uint32_t value =
				static_cast<uint32_t>(data[i]) << 16 |
				(remaining > 1 ? static_cast<uint32_t>(data[i + 1]) << 8 : 0) |
				(remaining > 2 ? static_cast<uint32_t>(data[i + 2]) : 0);
			encoded.push_back(alphabet[(value >> 18) & 0x3f]);
			encoded.push_back(alphabet[(value >> 12) & 0x3f]);
			encoded.push_back(remaining > 1 ? alphabet[(value >> 6) & 0x3f] : '=');
			encoded.push_back(remaining > 2 ? alphabet[value & 0x3f] : '=');
		}
		return encoded;
	}

	std::string CalculateFingerprint(
		const std::array<Sample, kSampleCount>& samples)
	{
		std::array<uint64_t, 4> channel_hashes = {};
		std::array<uint8_t, 4> means = {};
		std::array<uint8_t, 4> deviations = {};
		std::array<double, 4> sums = {};
		std::array<double, 4> squared_sums = {};
		size_t covered_alpha = 0;
		for (const Sample& sample : samples) {
			for (size_t component = 0; component < 4; ++component) {
				const float value = ClampUnit(sample.channels[component]);
				sums[component] += value;
				squared_sums[component] += value * value;
			}
			if (ClampUnit(sample.channels[3]) >= 0.5f)
				++covered_alpha;
		}
		for (size_t component = 0; component < 4; ++component) {
			const double mean = sums[component] / kSampleCount;
			const double variance = std::max(
				0.0,
				squared_sums[component] / kSampleCount - mean * mean);
			means[component] = QuantizeUnit(static_cast<float>(mean));
			deviations[component] =
				QuantizeUnit(static_cast<float>(std::sqrt(variance)));
		}
		const uint8_t alpha_coverage = QuantizeUnit(
			static_cast<float>(covered_alpha) /
			static_cast<float>(kSampleCount));

		const double pi = std::acos(-1.0);
		std::array<std::array<double, kResolution>, 8> basis = {};
		for (size_t index = 0; index < basis.size(); ++index) {
			const size_t frequency =
				(index * (kResolution - 1) + 3) / 7;
			for (size_t position = 0; position < kResolution; ++position) {
				basis[index][position] = std::cos(
					pi * (2.0 * position + 1.0) * frequency /
					(2.0 * kResolution));
			}
		}
		for (size_t component = 0; component < 4; ++component) {
			std::array<double, 64> coefficients = {};
			for (size_t v = 0; v < 8; ++v) {
				for (size_t u = 0; u < 8; ++u) {
					double coefficient = 0.0;
					for (size_t y = 0; y < kResolution; ++y) {
						const double basis_y = basis[v][y];
						for (size_t x = 0; x < kResolution; ++x) {
							coefficient +=
								ClampUnit(samples[y * kResolution + x].channels[component]) *
								basis[u][x] *
								basis_y;
						}
					}
					coefficients[v * 8 + u] = coefficient;
				}
			}
			std::array<double, 63> non_dc = {};
			std::copy(
				coefficients.begin() + 1,
				coefficients.end(),
				non_dc.begin());
			std::nth_element(
				non_dc.begin(),
				non_dc.begin() + non_dc.size() / 2,
				non_dc.end());
			const double median = non_dc[non_dc.size() / 2];
			uint64_t bit = 1;
			for (double coefficient : coefficients) {
				if (coefficient > median)
					channel_hashes[component] |= bit;
				bit <<= 1;
			}
		}

		std::array<uint8_t, 41> compact = {};
		size_t offset = 0;
		for (uint64_t hash : channel_hashes) {
			for (int byte = 7; byte >= 0; --byte)
				compact[offset++] =
					static_cast<uint8_t>((hash >> (byte * 8)) & 0xff);
		}
		for (uint8_t value : means)
			compact[offset++] = value;
		for (uint8_t value : deviations)
			compact[offset++] = value;
		compact[offset] = alpha_coverage;
		return
			"v3:r16:rgba8-phash:" +
			EncodeBase64Url(compact.data(), compact.size());
	}
}

int wmain(int argc, wchar_t** argv)
{
	if (argc != 2) {
		std::cerr << "Usage: r16_fingerprint.exe <texture.dds>\n";
		return 2;
	}

	DdsImage image;
	std::string error;
	if (!LoadDds(argv[1], &image, &error)) {
		std::cerr << error << ": " << Narrow(argv[1]) << '\n';
		return 3;
	}

	std::array<Sample, kSampleCount> samples = {};
	if (!SampleTexture(image, &samples, &error)) {
		std::cerr << error << ": " << Narrow(argv[1]) << '\n';
		return 4;
	}
	std::cout << CalculateFingerprint(samples) << '\n';
	return 0;
}
