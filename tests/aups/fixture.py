import purplship

proxy = purplship.gateway["aups"].create(
    {
        "server_url": "https://digitalapi.auspost.com.au",
        "username": "username",
        "password": "password",
        "account_number": "1234567",
    }
)
