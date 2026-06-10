/**
 * RecHunter — Login & Registration Page Controller
 */
(function () {
    'use strict';

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        bindAuth();
    }

    function bindAuth() {
        const tabLogin = $('#auth-tab-login');
        const tabRegister = $('#auth-tab-register');
        const formLogin = $('#auth-login-form');
        const formRegister = $('#auth-register-form');

        if (!tabLogin || !tabRegister) return;

        tabLogin.addEventListener('click', () => {
            tabLogin.classList.add('active');
            tabRegister.classList.remove('active');
            formLogin.style.display = '';
            formRegister.style.display = 'none';
        });

        tabRegister.addEventListener('click', () => {
            tabRegister.classList.add('active');
            tabLogin.classList.remove('active');
            formRegister.style.display = '';
            formLogin.style.display = 'none';
        });

        // Login Flow
        let loginUsername = '';
        let loginStep = 1; // 1: username, 2: OTP

        formLogin.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = $('#btn-login-submit');

            if (loginStep === 1) {
                loginUsername = $('#auth-login-username').value.trim();
                btn.disabled = true;
                btn.textContent = 'Sending code…';
                try {
                    await API.requestOTP(loginUsername);
                    Components.renderToast('Login code sent to Telegram!', 'success');
                    
                    $('.otp-group').style.display = '';
                    $('#auth-login-username').disabled = true;
                    btn.textContent = 'Verify Login Code';
                    loginStep = 2;
                } catch (err) {
                    Components.renderToast(`Login failed: ${err.message}`, 'error');
                }
                btn.disabled = false;
            } else if (loginStep === 2) {
                const code = $('#auth-login-otp').value.trim();
                btn.disabled = true;
                btn.textContent = 'Verifying…';
                try {
                    await API.verifyOTP(loginUsername, code);
                    Components.renderToast('Logged in successfully! Redirecting…', 'success');
                    setTimeout(() => {
                        window.location.href = '/';
                    }, 1000);
                } catch (err) {
                    Components.renderToast(`Verification failed: ${err.message}`, 'error');
                }
                btn.disabled = false;
            }
        });

        // Register Flow
        let regUsername = '';
        let regStep = 1; // 1: details, 2: OTP

        formRegister.addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = $('#btn-register-submit');

            if (regStep === 1) {
                regUsername = $('#auth-reg-username').value.trim();
                const phone = $('#auth-reg-telegram').value.trim();
                const apiKey = $('#auth-reg-callmebot').value.trim();

                btn.disabled = true;
                btn.textContent = 'Registering & sending code…';
                try {
                    await API.register({
                        username: regUsername,
                        phone_number: phone,
                        carrier_gateway: 'telegram',
                        callmebot_key: apiKey
                    });
                    Components.renderToast('Registration code sent to Telegram!', 'success');

                    $('.otp-group-reg').style.display = '';
                    $('#auth-reg-username').disabled = true;
                    $('#auth-reg-telegram').disabled = true;
                    $('#auth-reg-callmebot').disabled = true;
                    btn.textContent = 'Verify Activation Code';
                    regStep = 2;
                } catch (err) {
                    Components.renderToast(`Registration failed: ${err.message}`, 'error');
                }
                btn.disabled = false;
            } else if (regStep === 2) {
                const code = $('#auth-reg-otp').value.trim();
                btn.disabled = true;
                btn.textContent = 'Verifying…';
                try {
                    await API.verifyOTP(regUsername, code);
                    Components.renderToast('Registration complete! Redirecting…', 'success');
                    setTimeout(() => {
                        window.location.href = '/';
                    }, 1000);
                } catch (err) {
                    Components.renderToast(`Activation failed: ${err.message}`, 'error');
                }
                btn.disabled = false;
            }
        });
    }
})();
