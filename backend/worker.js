/*
 * Cloudflare Worker: Airoui Backend
 * Handles Auth, Products, Orders, and File Storage (R2)
 */
import bcrypt from 'bcryptjs';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // CORS Configuration (Allow all for development)
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    };

    // Handle OPTIONS (Preflight)
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      // Helper to return JSON
      const jsonResponse = (data, status = 200) =>
        new Response(JSON.stringify(data), { status, headers: { ...corsHeaders, 'Content-Type': 'application/json' } });

      // ==========================================
      // AI ROUTE (Cloudflare Workers AI - Llama 3)
      // ==========================================
      if (url.pathname === '/api/ai/chat' && request.method === 'POST') {
        const { messages, max_tokens, systemPrompt } = await request.json();

        // [NEW] Use provided system prompt or fallback to default
        const defaultSystemPrompt = "당신은 Airoui 소프트웨어 마켓의 전문 큐레이터이자 코딩 도우미입니다. 한국어로 친절하게 답변하세요.";
        const finalSystemPrompt = systemPrompt || defaultSystemPrompt;

        // [NEW] Construct messages with explicit system prompt at the beginning
        const fullMessages = [
          { role: "system", content: finalSystemPrompt },
          ...messages.filter(m => m.role !== 'system') // Avoid duplicate system messages
        ];

        // Cloudflare Workers AI (Llama 3)
        const aiResponse = await env.AI.run('@cf/meta/llama-3-8b-instruct', {
          messages: fullMessages,
          max_tokens: max_tokens || 256 // [NEW] Allow overrides, default 256
        });

        // Debug Log
        console.log('[Cloud Worker] AI Raw Response:', JSON.stringify(aiResponse));

        // Robust extraction
        let responseText = '';
        if (typeof aiResponse === 'string') {
          responseText = aiResponse;
        } else if (aiResponse && typeof aiResponse === 'object') {
          if (aiResponse.response) responseText = aiResponse.response;
          else if (aiResponse.result && aiResponse.result.response) responseText = aiResponse.result.response;
          else responseText = JSON.stringify(aiResponse); // Fallback
        }

        return new Response(JSON.stringify({ response: responseText }), {
          headers: { ...corsHeaders, 'Content-Type': 'application/json' }
        });
      }

      // ==========================================
      // FILE STORAGE ROUTES (R2) - [NEW]
      // ==========================================

      // 1. File Upload (관리자용)
      // PUT /api/upload?filename=my-app.zip
      if (url.pathname === '/api/upload' && request.method === 'PUT') {
        // 쿼리 파라미터에서 파일명 추출 (없으면 타임스탬프 기본값)
        const filename = url.searchParams.get('filename') || `file_${Date.now()}.bin`;

        // R2 버킷(MY_BUCKET)에 파일 저장 (Request Body 스트림을 그대로 저장)
        if (!env.MY_BUCKET) {
          return jsonResponse({ error: 'R2 Bucket not configured' }, 500);
        }
        await env.MY_BUCKET.put(filename, request.body);

        // 다운로드 가능한 공용 URL 생성 (Worker 자체를 프록시로 사용)
        const fileUrl = `${url.origin}/files/${filename}`;

        console.log(`[Cloud Worker] File Uploaded: ${filename}`);

        return jsonResponse({
          success: true,
          url: fileUrl
        });
      }

      // 2. File Download (공용)
      // GET /files/my-app.zip
      if (url.pathname.startsWith('/files/') && request.method === 'GET') {
        const filename = url.pathname.replace('/files/', '');

        if (!env.MY_BUCKET) {
          return new Response('R2 Bucket not configured', { status: 500, headers: corsHeaders });
        }

        // R2 버킷에서 파일 찾기
        const object = await env.MY_BUCKET.get(filename);

        if (object === null) {
          return new Response('File Not Found', { status: 404, headers: corsHeaders });
        }

        // 파일 데이터 전송 (메타데이터 및 헤더 설정)
        const headers = new Headers(object.httpMetadata);
        headers.set('etag', object.httpEtag);
        // CORs headers already set above
        // headers.set('Access-Control-Allow-Origin', '*'); 

        return new Response(object.body, { headers });
      }

      // 3. File Delete (Admin Only)
      // DELETE /api/upload?filename=my-app.zip
      if (url.pathname === '/api/upload' && request.method === 'DELETE') {
        const filename = url.searchParams.get('filename');

        if (!filename) return jsonResponse({ error: 'Filename required' }, 400);

        if (!env.MY_BUCKET) {
          return jsonResponse({ error: 'R2 Bucket not configured' }, 500);
        }

        await env.MY_BUCKET.delete(filename);

        console.log(`[Cloud Worker] File Deleted: ${filename}`);

        return jsonResponse({ success: true, filename });
      }


      // ==========================================
      // AUTH ROUTES
      // ==========================================

      // 1. Sign Up
      if (url.pathname === '/api/auth/signup' && request.method === 'POST') {
        const { email, password, nickname } = await request.json();

        if (!email || !password) return jsonResponse({ error: 'Missing fields' }, 400);

        // Check duplicate
        const existing = await env.DB.prepare('SELECT id FROM users WHERE email = ?').bind(email).first();
        if (existing) return jsonResponse({ error: 'Email already exists' }, 409);

        // Hash Password
        const salt = await bcrypt.genSalt(10);
        const hash = await bcrypt.hash(password, salt);

        // Save
        const result = await env.DB.prepare(
          'INSERT INTO users (email, password_hash, nickname, role, points, coupons) VALUES (?, ?, ?, ?, ?, ?)'
        ).bind(email, hash, nickname || 'User', 'user', 0, 10).run(); // 0 Points, 10 Coupons

        // Debug Log
        console.log(`[Cloud Worker] New User Signed Up: ${email} with 10 Coupons`);

        return jsonResponse({ success: true, userId: result.meta.last_row_id });
      }

      // 2. Login
      if (url.pathname === '/api/auth/login' && request.method === 'POST') {
        const { email, password } = await request.json();

        const user = await env.DB.prepare('SELECT * FROM users WHERE email = ?').bind(email).first();
        if (!user) return jsonResponse({ error: 'Invalid credentials' }, 401);

        const match = await bcrypt.compare(password, user.password_hash);
        if (!match) return jsonResponse({ error: 'Invalid credentials' }, 401);

        // Remove sensitive data
        delete user.password_hash;
        console.log(`[Cloud Worker] User Logged In: ${email}, Coupons: ${user.coupons}`);

        return jsonResponse({ success: true, user });
      }

      // 3. Delete Account
      if (url.pathname === '/api/auth/delete' && request.method === 'POST') {
        const { userId, password } = await request.json();

        // Verify User
        const user = await env.DB.prepare('SELECT * FROM users WHERE id = ?').bind(userId).first();
        if (!user) return jsonResponse({ error: 'User not found' }, 404);

        const match = await bcrypt.compare(password, user.password_hash);
        if (!match) return jsonResponse({ error: 'Invalid password' }, 401);

        // Delete Data
        // Let's keep it simple: Delete User, Orders, and their Products.
        await env.DB.batch([
          env.DB.prepare('DELETE FROM users WHERE id = ?').bind(userId),
          env.DB.prepare('DELETE FROM orders WHERE user_id = ?').bind(userId),
          env.DB.prepare('DELETE FROM products WHERE user_id = ?').bind(userId)
        ]);

        console.log(`[Cloud Worker] User Deleted: ${userId}`);
        return jsonResponse({ success: true });
      }

      // ==========================================
      // PRODUCT ROUTES
      // ==========================================

      // 4. Get All Products
      if (url.pathname === '/api/products' && request.method === 'GET') {
        const { results } = await env.DB.prepare('SELECT * FROM products ORDER BY created_at DESC').all();
        // Parse JSON fields
        const products = results.map(p => ({
          ...p,
          details: JSON.parse(p.details || '{}'),
          stats: JSON.parse(p.stats || '{}')
        }));
        return jsonResponse(products);
      }

      // [NEW] 5-1. Delete Product (Admin Only)
      if (url.pathname === '/api/products' && request.method === 'DELETE') {
        const id = url.searchParams.get('id');

        if (!id) return jsonResponse({ error: 'Product ID required' }, 400);

        // 1. DB에서 삭제
        await env.DB.prepare('DELETE FROM products WHERE id = ?').bind(id).run();

        // (선택 사항) 2. 관련 주문 항목도 삭제하거나 유지할지 정책 결정 필요
        // 여기서는 상품만 깔끔하게 삭제합니다.

        return jsonResponse({ success: true, id });
      }

      // 5. Add or Update Product (Admin Only)
      if (url.pathname === '/api/products' && request.method === 'POST') {
        const product = await request.json();

        // [핵심 수정 1] 누락되었던 세부 정보(fileSize, license 등)를 details에 모두 포함
        const details = JSON.stringify({
          tags: product.tags || [],
          features: product.features || [],
          images: product.images || [],
          compatibility: product.compatibility || '',
          fileSize: product.fileSize || '',       // 추가됨
          license: product.license || '',         // 추가됨
          downloadUrl: product.downloadUrl || '', // 추가됨
          fileData: product.fileData || null,
          sourceCode: product.sourceCode || ''    // 소스코드도 details 백업용으로 저장
        });

        const stats = JSON.stringify({
          views: product.views || 0,
          downloads: product.downloads || 0
        });

        // [핵심 수정 2] INSERT -> INSERT OR REPLACE (수정 시 덮어쓰기 가능하도록 변경)
        await env.DB.prepare(`
            INSERT OR REPLACE INTO products (id, user_id, name, price, category_id, description, source_code, version, details, stats)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `).bind(
          product.id,
          product.authorId || 0,
          product.name,
          product.price,
          product.categoryId,
          product.description,
          product.sourceCode,
          product.version,
          details,
          stats
        ).run();

        return jsonResponse({ success: true });
      }

      // ==========================================
      // ORDER ROUTES
      // ==========================================

      // 6. Create Order
      if (url.pathname === '/api/orders' && request.method === 'POST') {
        const { userId, items, total } = await request.json();

        // 1. Get User Data
        const user = await env.DB.prepare('SELECT points, coupons FROM users WHERE id = ?').bind(userId).first();
        if (!user) return jsonResponse({ error: 'User not found' }, 404);

        let paymentMethod = 'points';
        let remainingPoints = user.points;
        let remainingCoupons = user.coupons || 0;
        let finalTotal = total;

        const itemCount = items.length;

        // 2. Determine Payment Method (Priority: Coupons)
        if (remainingCoupons >= itemCount) {
          // Pay with Coupons
          paymentMethod = 'coupon';
          remainingCoupons -= itemCount;
          finalTotal = 0; // Paid with coupons

          // Update User Coupons
          await env.DB.prepare('UPDATE users SET coupons = ? WHERE id = ?').bind(remainingCoupons, userId).run();
          console.log(`[Cloud Worker] Order paid with Coupons. Used: ${itemCount}, Remaining: ${remainingCoupons}`);

        } else {
          // Pay with Points
          if (user.points < total) {
            return jsonResponse({ error: 'Insufficient points and coupons' }, 402);
          }
          paymentMethod = 'points';
          remainingPoints -= total;

          // Update User Points
          await env.DB.prepare('UPDATE users SET points = ? WHERE id = ?').bind(remainingPoints, userId).run();
          console.log(`[Cloud Worker] Order paid with Points. Used: ${total}, Remaining: ${remainingPoints}`);
        }

        // 3. Create Order
        const orderId = 'ord_' + Date.now();
        await env.DB.prepare('INSERT INTO orders (id, user_id, total_amount, payment_method) VALUES (?, ?, ?, ?)').bind(orderId, userId, finalTotal, paymentMethod).run();

        // 4. Save Items (Batch)
        const stmt = env.DB.prepare('INSERT INTO order_items (order_id, product_id, product_name, price) VALUES (?, ?, ?, ?)');
        const batch = items.map(item => stmt.bind(orderId, item.productId, item.productName, item.price));
        await env.DB.batch(batch);

        return jsonResponse({ success: true, orderId, remainingPoints, remainingCoupons });
      }

      return new Response('Not Found', { status: 404, headers: corsHeaders });

    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
    }
  }
};