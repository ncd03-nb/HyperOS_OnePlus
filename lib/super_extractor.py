#!/usr/bin/env python3
"""Read single-device Android raw/sparse super images without unsparsing a copy.

Format: AOSP system/core/fs_mgr/liblp/include/liblp/metadata_format.h and
system/core/libsparse/sparse_format.h. Geometry/header/table SHA256 are checked.
"""
import bisect
import hashlib
from pathlib import Path
import re
import struct

SPARSE_MAGIC = 0xed26ff3a
GEOMETRY_MAGIC = 0x616c4467
HEADER_MAGIC = 0x414c5030
SECTOR = 512
BLOCK = 1 << 20


class ImageReader:
    def __init__(self, path):
        self.file = open(path, 'rb')
        self.physical_size = Path(path).stat().st_size
        self.size = self.physical_size
        self.chunks = []
        try:
            prefix = self._physical(0, min(28, self.physical_size))
            self.sparse = len(prefix) >= 4 and struct.unpack_from('<I', prefix)[0] == SPARSE_MAGIC
            if self.sparse:
                self._index_sparse(prefix)
            self.starts = [chunk[0] for chunk in self.chunks]
        except Exception:
            self.close()
            raise

    def close(self):
        self.file.close()

    def _physical(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.physical_size:
            raise ValueError('Truncated image')
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise ValueError('Truncated image')
        return data

    def _index_sparse(self, prefix):
        if len(prefix) != 28:
            raise ValueError('Truncated sparse header')
        _, major, _, header_size, chunk_header, block_size, blocks, count, _ = struct.unpack('<I4H4I', prefix)
        if major != 1 or header_size < 28 or chunk_header < 12 or not block_size or block_size % 4:
            raise ValueError('Unsupported sparse header')
        if count > (self.physical_size - header_size) // chunk_header:
            raise ValueError('Invalid sparse chunk count')
        physical, logical = header_size, 0
        for _ in range(count):
            kind, _, chunk_blocks, total = struct.unpack('<2H2I', self._physical(physical, 12))
            length = chunk_blocks * block_size
            payload = total - chunk_header
            data_offset = physical + chunk_header
            if total < chunk_header or physical + total > self.physical_size:
                raise ValueError('Invalid sparse chunk size')
            if kind == 0xcac1 and payload == length and length:
                value = data_offset
            elif kind == 0xcac2 and payload == 4 and length:
                value = self._physical(data_offset, 4)
            elif kind == 0xcac3 and payload == 0 and length:
                value = None
            elif kind == 0xcac4 and payload == 4 and not length:
                # CRC32 chunks do not contribute logical blocks. LP metadata
                # checksums are verified below; sparse-image CRC is not claimed.
                physical += total
                continue
            else:
                raise ValueError('Unsupported or malformed sparse chunk')
            self.chunks.append((logical, length, kind, value))
            logical += length
            physical += total
        if logical != blocks * block_size:
            raise ValueError('Sparse logical size does not match header')
        self.size = logical

    def read_at(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError('Extent outside super image')
        if not self.sparse:
            return self._physical(offset, size)
        result = bytearray()
        while size:
            index = bisect.bisect_right(self.starts, offset) - 1
            if index < 0:
                raise ValueError('Invalid sparse offset')
            start, length, kind, value = self.chunks[index]
            relative = offset - start
            take = min(size, length - relative)
            if take <= 0:
                raise ValueError('Invalid sparse chunk range')
            if kind == 0xcac1:
                result.extend(self._physical(value + relative, take))
            elif kind == 0xcac2:
                phase = relative % 4
                result.extend((value * ((take + phase + 3) // 4))[phase:phase + take])
            else:
                result.extend(bytes(take))
            offset += take
            size -= take
        return bytes(result)


def _checksum(data, start, end):
    return hashlib.sha256(data[:start] + bytes(end - start) + data[end:]).digest() == data[start:end]


def _geometry(reader):
    errors = []
    for offset in (4096, 8192):
        try:
            data = reader.read_at(offset, 52)
            magic, size = struct.unpack_from('<2I', data)
            if magic != GEOMETRY_MAGIC or not 52 <= size <= 4096:
                raise ValueError('Invalid LP geometry')
            data = reader.read_at(offset, size)
            if not _checksum(data, 8, 40):
                raise ValueError('LP geometry checksum mismatch')
            maximum, slots, block_size = struct.unpack_from('<3I', data, 40)
            if not 128 <= maximum <= 16 << 20 or maximum % SECTOR or not 1 <= slots <= 32 or not block_size or block_size % SECTOR:
                raise ValueError('Invalid LP geometry dimensions')
            return maximum, slots
        except ValueError as error:
            errors.append(str(error))
    raise ValueError('; '.join(errors))


def read_metadata(reader):
    maximum, slots = _geometry(reader)
    errors = []
    for offset in (12288, 12288 + maximum * slots):
        try:
            prefix = reader.read_at(offset, 128)
            magic, major, minor, header_size = struct.unpack_from('<I2HI', prefix)
            table_size = struct.unpack_from('<I', prefix, 44)[0]
            if magic != HEADER_MAGIC or major != 10 or minor > 2 or not 128 <= header_size <= maximum or table_size > maximum - header_size:
                raise ValueError('Unsupported LP metadata header')
            header = reader.read_at(offset, header_size)
            if not _checksum(header, 12, 44):
                raise ValueError('LP header checksum mismatch')
            tables = reader.read_at(offset + header_size, table_size)
            if hashlib.sha256(tables).digest() != header[48:80]:
                raise ValueError('LP tables checksum mismatch')

            def table(descriptor, minimum):
                start, count, stride = struct.unpack_from('<3I', header, descriptor)
                if stride < minimum or start + count * stride > len(tables):
                    raise ValueError('Invalid LP table bounds')
                return [tables[start + index * stride:start + index * stride + minimum] for index in range(count)]

            partitions = table(80, 52)
            extents = [struct.unpack('<QIQI', data) for data in table(92, 24)]
            groups = table(104, 48)
            devices = table(116, 64)
            if not devices:
                raise ValueError('Super image has no block device')
            result = {}
            for data in partitions:
                name, attrs, first, count, group = struct.unpack('<36s4I', data)
                name = name.split(b'\0', 1)[0].decode('ascii')
                if not re.fullmatch(r'[A-Za-z0-9_]+', name) or name in result or first + count > len(extents) or group >= len(groups):
                    raise ValueError('Invalid logical partition entry')
                selected = extents[first:first + count]
                for sectors, kind, sector, source in selected:
                    if kind not in (0, 1) or (kind == 0 and source >= len(devices)):
                        raise ValueError('Invalid logical partition extent')
                    if kind == 0 and source == 0 and (sector + sectors) * SECTOR > reader.size:
                        raise ValueError('Logical partition extends outside super image')
                result[name] = {'extents': selected, 'disabled': bool(attrs & 8),
                                'size': sum(extent[0] * SECTOR for extent in selected)}
            return result
        except (ValueError, UnicodeError) as error:
            errors.append(str(error))
    raise ValueError('; '.join(errors))


def extract(path, output, parts):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    reader = ImageReader(path)
    try:
        metadata = read_metadata(reader)
        jobs = {}
        for part in parts:
            if not re.fullmatch(r'[A-Za-z0-9_]+', part):
                raise ValueError('Invalid requested partition name')
            name = next((name for name in (part, part + '_a', part + '_b')
                         if name in metadata and metadata[name]['size'] and not metadata[name]['disabled']), None)
            if name is None:
                raise ValueError(f'{part}: no populated partition in super image')
            if any(kind == 0 and source != 0 for _, kind, _, source in metadata[name]['extents']):
                raise ValueError('Multi-device/retrofit super images need their other physical images')
            if (output / (part + '.img')).exists():
                raise ValueError(f'Refusing to overwrite {part}.img; choose a fresh input directory')
            jobs[part] = name
        report = {}
        for part, name in jobs.items():
            temporary = output / (part + '.img.part')
            digest = hashlib.sha256()
            with temporary.open('xb') as stream:
                for sectors, kind, sector, _ in metadata[name]['extents']:
                    remaining, position = sectors * SECTOR, sector * SECTOR
                    while remaining:
                        take = min(remaining, BLOCK)
                        data = reader.read_at(position, take) if kind == 0 else bytes(take)
                        digest.update(data)
                        if kind == 1:
                            stream.seek(take, 1)
                        else:
                            stream.write(data)
                        remaining -= take
                        position += take
                stream.truncate(metadata[name]['size'])
            temporary.rename(output / (part + '.img'))
            report[part] = {'logical_partition': name, 'size_bytes': metadata[name]['size'],
                            'sha256': digest.hexdigest(), 'sparse_super': reader.sparse}
        return report
    finally:
        reader.close()
